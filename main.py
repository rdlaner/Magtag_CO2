import adafruit_minimqtt.adafruit_minimqtt as MQTT
import adafruit_scd30
import alarm
import digitalio
import board
import busio
import json
import socketpool
import ssl
import time
import wifi

from adafruit_magtag.magtag import MagTag
from config import config
from display import MagtagDisplay
from homeassistant.device import HomeAssistantDevice
from homeassistant.number import HomeAssistantNumber
from homeassistant.sensor import HomeAssistantSensor
from homeassistant.device_class import DeviceClass
from memory import NonVolatileMemory
from micropython import const
from network import MagtagNetwork
from secrets import secrets
from supervisor import runtime, reload


# NOTE: SCD30 takes a few seconds to produce data after sample_rate period
# NOTE: (Re-)Initializing SCD30/I2C bus before its measurement interval
# has finished means the interval will reset, effectively doubling the interval
# in deep sleep mode.
# But.... even after waiting, (Re-)Initializing the SCD30/I2C bus will also
# reset the `data_available` available property, resulting in the same effect
# of doubling the measurement interval.
# Boooo!
# So instead, I have deep sleep mode sample multiple times at 2 secs on start up
# and then essentially disable measurements until the next startup. That way no
# samples are impacted by this re-initialization issue.
# NOTE: Calling scd30.reset() also resets its temperature, CO2 (and other)
# compensation algorithms. So, we don't want to call reset() during the init
# process when in deep sleep mode otherwise the SCD30 would be able to fully
# apply its compensation algorithms.
# NOTE: Also, stopping continuous mode, changing measurement interval, and then
# resuming continuous mode as a work-around to get a new measurement interval
# operating immediately, unfortunately has the same impact as calling reset()
# in that the ppm calibration algorithm is not allowed to fully run since I
# would do this on every sleep cycle.
# NOTE: For some reason, it seems that when I updated the measurement interval
# immediately after reading the sensors in Process(), the new interval gets
# written to the co2 cal ref instead. If I move the interval update back to
# the main loop after Process() is called, I don't see this issue. Really weird.
# There possibly is some connection between updating measurement interval and
# manual calibration reference??

# TODO: Battery improvement - collect several samples and publish them at longer intervals
# TODO: Improve display
# TODO: Add config for publishing logs to mqtt
# TODO: Fix circuitpython scd30 init which forces a 2 second measurement interval
# TODO: Add base class for HA types (eg for sensor, number, etc.)

# Constants
MQTT_CONNECT_ATTEMPTS = const(10)
MQTT_RX_TIMEOUT_SEC = const(10)
MQTT_KEEP_ALIVE_MARGIN_SEC = const(20)
FORCE_CAL_DISABLED = const(-1)
MAGTAG_BATT_DXN_VOLTAGE = 4.20
SENSOR_NAME_BATTERY = "Batt Voltage"
SENSOR_NAME_SCD30_CO2 = "SCD30 CO2"
SENSOR_NAME_SCD30_HUM = "SCD30 Humidity"
SENSOR_NAME_SCD30_TEMP = "SCD30 Temperature"
NUMBER_NAME_TEMP_OFFSET = "Temp Offset"
NUMBER_NAME_PRESSURE = "Pressure"
NUMBER_NAME_CO2_REF = "CO2 Ref"
NON_VOL_NAME_PRESSURE = "pressure"
NON_VOL_NAME_CAL = "forced cal"
NON_VOL_NAME_TEMP_OFFSET = "temp offset"

# Globals
display_time = time.monotonic()
state_light_sleep = runtime.serial_connected if not config["force_deep_sleep"] else False
non_volatile_memory = NonVolatileMemory()


def c_to_f(temp_cels: float) -> float:
    return (temp_cels * 1.8) + 32.0 if temp_cels else None


def mqtt_connected(client: MQTT.MQTT, user_data, flags, rc) -> None:
    # This function will be called when the client is connected
    # successfully to the broker.
    print(f"Subscribing to {config['pressure_topic']}...")
    client.subscribe(config["pressure_topic"])
    print(f"Subscribing to {config['cmd_topic']}...")
    client.subscribe(config["cmd_topic"])


def mqtt_disconnected(client: MQTT.MQTT, user_data, rc) -> None:
    # This method is called when the client is disconnected
    print("Disconnected from MQTT Broker!")


def mqtt_message(client: MQTT.MQTT, topic: str, message) -> None:
    """Method called when a client's subscribed feed has a new
    value.
    :param str topic: The topic of the feed with a new value.
    :param str message: The new value
    """
    print("New message on topic {0}: {1}".format(topic, message))
    if topic == config["pressure_topic"]:
        try:
            pressure = round(float(message))
        except ValueError as e:
            print("Ambient pressure value invalid")
            print(e)
            return

        print(f"Updating nv pressure to {pressure}")
        non_volatile_memory.set_element(NON_VOL_NAME_PRESSURE, pressure)

    elif topic == config["cmd_topic"]:
        try:
            obj = json.loads(message)
        except ValueError as e:
            print("Forced calibration value invalid")
            print(e)
            return

        if NUMBER_NAME_CO2_REF in obj:
            cal_val = obj[NUMBER_NAME_CO2_REF]
            print(f"Updating nv cal to {cal_val}")
            non_volatile_memory.set_element(NON_VOL_NAME_CAL, cal_val)

        if NUMBER_NAME_TEMP_OFFSET in obj:
            temp_offset = obj[NUMBER_NAME_TEMP_OFFSET]
            print(f"Updating nv temp offset to {temp_offset}")
            non_volatile_memory.set_element(NON_VOL_NAME_TEMP_OFFSET, temp_offset)


def scd30_init(scd30: adafruit_scd30.SCD30) -> None:
    if state_light_sleep:
        measurement_interval = config["light_sleep_sec"]
    else:
        measurement_interval = config["deep_sleep_sec"]

    if scd30.self_calibration_enabled:
        print("Updating self cal mode to OFF")
        scd30.self_calibration_enabled = False
    if scd30.temperature_offset != config["temp_offset_c"]:
        print(f"Updating temperature offset to {config['temp_offset_c']}")
        scd30.temperature_offset = config["temp_offset_c"]
    if scd30.measurement_interval != measurement_interval:
        print(f"Updating measurement interval to {measurement_interval}")
        scd30.measurement_interval = measurement_interval

    # Start continuous mode
    pressure = non_volatile_memory.get_element(NON_VOL_NAME_PRESSURE)
    print(f"Updating SCD30 pressure to {pressure} and enabling continuous mode")
    scd30.ambient_pressure = pressure

    print("SCD30 Config:")
    print(f"self cal enabled: {scd30.self_calibration_enabled}")
    print(f"measurement_interval: {scd30.measurement_interval}")
    print(f"temperature_offset: {scd30.temperature_offset}")
    print(f"ambient_pressure: {scd30.ambient_pressure}")
    print(f"CO2 cal ref: {scd30.forced_recalibration_reference}")
    print(f"display_refresh_rate_sec: {config['display_refresh_rate_sec']}")


def main() -> None:
    global display_time
    print("\nInitializing...")
    print("Time: %0.2f" % time.monotonic())

    # Initialization
    if not alarm.wake_alarm:
        # Initialize persistent data
        non_volatile_memory.reset()
        non_volatile_memory.add_element(
            NON_VOL_NAME_PRESSURE, "I", config["ambient_pressure"])
        non_volatile_memory.add_element(
            NON_VOL_NAME_CAL, "i", FORCE_CAL_DISABLED)
        non_volatile_memory.add_element(
            NON_VOL_NAME_TEMP_OFFSET, "f", config["temp_offset_c"]
        )
    non_volatile_memory.print_elements()
    current_pressure = non_volatile_memory.get_element(NON_VOL_NAME_PRESSURE)
    current_temp_offset = non_volatile_memory.get_element(NON_VOL_NAME_TEMP_OFFSET)
    current_cal_val = non_volatile_memory.get_element(NON_VOL_NAME_CAL)

    red_led = digitalio.DigitalInOut(board.D13)
    red_led.switch_to_output(value=False)

    magtag = MagTag()
    magtag.peripherals.neopixel_disable = True
    magtag.peripherals.speaker_disable = True

    display = MagtagDisplay()
    data_read_alarm = alarm.pin.PinAlarm(pin=board.D10, value=True, pull=False)

    i2c = busio.I2C(board.SCL, board.SDA, frequency=50000)
    scd30 = adafruit_scd30.SCD30(i2c)
    scd30_init(scd30)

    socket_pool = socketpool.SocketPool(wifi.radio)
    keep_alive_sec = config["light_sleep_sec"] if state_light_sleep else config["deep_sleep_sec"]
    keep_alive_sec += MQTT_KEEP_ALIVE_MARGIN_SEC
    mqtt_client = MQTT.MQTT(
        broker=secrets["mqtt_broker"],
        port=secrets["mqtt_port"],
        username=secrets["mqtt_username"],
        password=secrets["mqtt_password"],
        socket_pool=socket_pool,
        ssl_context=ssl.create_default_context(),
        connect_retries=MQTT_CONNECT_ATTEMPTS,
        recv_timeout=MQTT_RX_TIMEOUT_SEC,
        keep_alive=keep_alive_sec
    )
    mqtt_client.on_connect = mqtt_connected
    mqtt_client.on_disconnect = mqtt_disconnected
    mqtt_client.on_message = mqtt_message
    network = MagtagNetwork(magtag, mqtt_client)

    def read_batt():
        volts = magtag.peripherals.battery
        return volts if volts < MAGTAG_BATT_DXN_VOLTAGE else 0

    # Create home assistant sensors
    sensor_battery = HomeAssistantSensor(
        SENSOR_NAME_BATTERY, read_batt, 2, DeviceClass.BATTERY, "V")
    sensor_scd30_co2 = HomeAssistantSensor(
        SENSOR_NAME_SCD30_CO2, lambda: scd30.CO2, 0, DeviceClass.CARBON_DIOXIDE, "ppm")
    sensor_scd30_hum = HomeAssistantSensor(
        SENSOR_NAME_SCD30_HUM, lambda: scd30.relative_humidity, 0, DeviceClass.HUMIDITY, "%")
    sensor_scd30_temp = HomeAssistantSensor(
        SENSOR_NAME_SCD30_TEMP, lambda: scd30.temperature, 1, DeviceClass.TEMPERATURE, "°C")

    # Create home assistant numbers
    number_temp_offset = HomeAssistantNumber(
        NUMBER_NAME_TEMP_OFFSET,
        lambda: non_volatile_memory.get_element(NON_VOL_NAME_TEMP_OFFSET),
        precision=1,
        unit="°C",
        min_value=0,
        mode="box")
    number_pressure = HomeAssistantNumber(
        NUMBER_NAME_PRESSURE,
        lambda: non_volatile_memory.get_element(NON_VOL_NAME_PRESSURE),
        device_class=DeviceClass.PRESSURE,
        unit="mbar",
        min_value=100,
        max_value=1100,
        mode="box")
    number_co2_ref = HomeAssistantNumber(
        NUMBER_NAME_CO2_REF,
        lambda: non_volatile_memory.get_element(NON_VOL_NAME_CAL),
        device_class=DeviceClass.CARBON_DIOXIDE,
        unit="ppm",
        min_value=400,
        max_value=5000,
        mode="box")

    # Create home assistant device
    co2_device = HomeAssistantDevice(DEVICE_NAME, "Magtag", mqtt_client)
    co2_device.add_sensor(sensor_scd30_co2)
    co2_device.add_sensor(sensor_scd30_hum)
    co2_device.add_sensor(sensor_scd30_temp)
    co2_device.add_sensor(sensor_battery)
    co2_device.add_number(number_temp_offset)
    co2_device.add_number(number_pressure)
    co2_device.add_number(number_co2_ref)

    # Set command topic
    config["cmd_topic"] = f"{co2_device.number_topic}/cmd"

    display_time = time.monotonic()
    print("Time: %0.2f" % time.monotonic())
    print("")

    # Main Loop
    while True:
        print("Processing...")
        print("Time: %0.2f" % time.monotonic())

        print("Reading sensors...")
        sensor_data = co2_device.read_sensors(cache=True)
        print(sensor_data)
        print("Time: %0.2f" % time.monotonic())

        # Connect network, if not already
        # Connecting here allows deep sleep to read data prior to taking a higher
        # power draw when connecting the network services.
        if not network.is_connected():
            network.connect()

            # Send home assistant mqtt discovery
            try:
                co2_device.send_discovery()
            except (ValueError, RuntimeError, MQTT.MMQTTException) as e:
                print("CO2 device MQTT discovery failure, rebooting\n", e)
                reload()

        # Service MQTT
        print("Time: %0.2f" % time.monotonic())
        network.loop(recover=state_light_sleep)

        # Publish data to MQTT
        print("Time: %0.2f" % time.monotonic())
        try:
            print("Publishing MQTT data...")
            co2_device.publish_numbers()
            co2_device.publish_sensors()
        except (OSError, ValueError, RuntimeError, MQTT.MMQTTException) as e:
            print("MQTT Publish failure\n", e)
            if state_light_sleep:
                network.recover()
            else:
                return

        # Turn off network if in deep sleep mode
        if not state_light_sleep:
            network.disconnect()

        # Perform forced recal if received new cal value
        expected_cal_val = non_volatile_memory.get_element(NON_VOL_NAME_CAL)
        if expected_cal_val != FORCE_CAL_DISABLED and expected_cal_val != current_cal_val:
            print(f"Updating cal reference from {current_cal_val} to {expected_cal_val}")
            current_cal_val = expected_cal_val
            scd30.forced_recalibration_reference = expected_cal_val

        # Update temp offset if received a new value
        expected_temp_offset = non_volatile_memory.get_element(NON_VOL_NAME_TEMP_OFFSET)
        if expected_temp_offset != current_temp_offset:
            print(f"Updating temp offset from {current_temp_offset} to {expected_temp_offset}")
            current_temp_offset = expected_temp_offset
            scd30.temperature_offset = expected_temp_offset

        # Update pressure if received a new value
        expected_pressure = non_volatile_memory.get_element(NON_VOL_NAME_PRESSURE)
        if expected_pressure != current_pressure:
            print(f"Updating pressure from {current_pressure} to {expected_pressure}")
            current_pressure = expected_pressure            
            scd30.ambient_pressure = expected_pressure

        # Update display
        if ((time.monotonic() - display_time) >= config["display_refresh_rate_sec"]) or not state_light_sleep:
            print("Time: %0.2f" % time.monotonic())
            print("Updating display...")
            display.update_co2(sensor_data[SENSOR_NAME_SCD30_CO2])
            display.update_temp(c_to_f(sensor_data[SENSOR_NAME_SCD30_TEMP]))
            display.update_hum(sensor_data[SENSOR_NAME_SCD30_HUM])
            display.update_batt(sensor_data[SENSOR_NAME_BATTERY])
            display.update_usb("T" if runtime.serial_connected else "F")
            display.refresh()
            display_time = time.monotonic()

        print("Time: %0.2f" % time.monotonic())
        print("")

        print("Sleeping...")
        print("")
        if state_light_sleep:
            if state_light_sleep != runtime.serial_connected:
                reload()  # State transition, reboot into deep sleep state
            else:
                magtag.enter_light_sleep(data_read_alarm)
        else:
            if state_light_sleep != runtime.serial_connected and config["force_deep_sleep"] is False:
                reload()  # State transition, reboot into light sleep state
            else:
                magtag.exit_and_deep_sleep(data_read_alarm)


main()
