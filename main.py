import adafruit_minimqtt.adafruit_minimqtt as MQTT
import adafruit_scd30
import alarm
import digitalio
import board
import busio
import socketpool
import ssl
import struct
import time
import wifi

from adafruit_magtag.magtag import MagTag
from config import config
from display import MagtagDisplay
from homeassistant.device import HomeAssistantDevice
from homeassistant.sensor import HomeAssistantSensor
from homeassistant.device_class import DeviceClass
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

# TODO: get access to scd data-ready pin and use it to trigger "process" -
#       Can use this to fix current measurement_interval issue
# TODO: Battery improvement - collect several samples and publish them at longer intervals
# TODO: Add config for publishing logs to mqtt

# Constants
DEEP_SLEEP_THROW_AWAY_SAMPLES = 2
MQTT_CONNECT_ATTEMPTS = 10
MQTT_RX_TIMEOUT_SEC = 10
MQTT_KEEP_ALIVE_MARGIN_SEC = 20
SCD30_SAMPLE_RATE_FAST_SEC = 2
SCD30_SAMPLE_RATE_SLOW_SEC = config["deep_sleep_sec"] - 5
DEVICE_NAME = "Magtag_CO2"
SENSOR_NAME_BATTERY = "Batt Voltage"
SENSOR_NAME_SCD30_CO2 = "SCD30 CO2"
SENSOR_NAME_SCD30_HUM = "SCD30 Humidity"
SENSOR_NAME_SCD30_TEMP = "SCD30 Temperature"

# Globals
display_time = time.monotonic()
state_light_sleep = runtime.serial_connected if not config["force_deep_sleep"] else False


def c_to_f(temp_cels: float) -> float:
    return (temp_cels * 1.8) + 32.0


def mqtt_connected(client: MQTT.MQTT, userdata, flags, rc) -> None:
    # This function will be called when the client is connected
    # successfully to the broker.
    print("Connected to MQTT broker!")
    client.subscribe(config["pressure_topic"])


def mqtt_disconnected(client: MQTT.MQTT, userdata, rc) -> None:
    # This method is called when the client is disconnected
    print("Disconnected from MQTT Broker!")


def mqtt_message(client: MQTT.MQTT, topic: str, message) -> None:
    """Method callled when a client's subscribed feed has a new
    value.
    :param str topic: The topic of the feed with a new value.
    :param str message: The new value
    """
    print("New message on topic {0}: {1}".format(topic, message))
    if topic == config["pressure_topic"]:
        try:
            pressure = bytearray(struct.pack("i", round(float(message))))
        except ValueError:
            print("Ambient pressure not updated")
            return

        for i, data in enumerate(pressure):
            alarm.sleep_memory[i] = data

        print(f"Updated ambient pressure to: {round(float(message))}")


def process(co2_device: HomeAssistantDevice,
            display: MagtagDisplay,
            network: MagtagNetwork) -> None:
    global display_time
    print("Processing...")
    print("Time: %0.2f" % time.monotonic())

    # Read sensors
    if state_light_sleep:
        sensor_data = co2_device.read_sensors(cache=True)
    else:
        # Read multiple samples and only use last one
        for i in range(DEEP_SLEEP_THROW_AWAY_SAMPLES):
            sensor_data = co2_device.read_sensors(cache=False)
            print("Time: %0.2f" % time.monotonic())
            print(sensor_data)

        sensor_data = co2_device.read_sensors(cache=True)

    # Publish data to MQTT
    print("MQTT Publish Time: %0.2f" % time.monotonic())
    try:
        co2_device.publish_sensors()
    except (OSError, ValueError, RuntimeError, MQTT.MMQTTException) as e:
        print("MQTT Publish failure\n", e)
        if state_light_sleep:
            network.recover()
        else:
            return

    # Service MQTT
    print("MQTT Time Loop: %0.2f" % time.monotonic())
    network.loop(recover=state_light_sleep)

    # Update display
    if ((time.monotonic() - display_time) >= config["display_refresh_rate_sec"]) or not state_light_sleep:
        print("Display Time: %0.2f" % time.monotonic())
        display.update_co2(sensor_data[SENSOR_NAME_SCD30_CO2])
        display.update_temp(c_to_f(sensor_data[SENSOR_NAME_SCD30_TEMP]))
        display.update_hum(sensor_data[SENSOR_NAME_SCD30_HUM])
        display.update_batt(sensor_data[SENSOR_NAME_BATTERY])
        display.update_usb("T" if runtime.serial_connected else "F")
        display.refresh()
        display_time = time.monotonic()

    print("Done Processing Time: %0.2f" % time.monotonic())
    print(sensor_data)
    print("")


def scd30_init(scd30: adafruit_scd30.SCD30) -> None:
    if state_light_sleep:
        measurement_interval = config["light_sleep_sec"]
    else:
        measurement_interval = SCD30_SAMPLE_RATE_FAST_SEC

    # NOTE: Probably should remove this as using ambient pressure comp invalidates
    #       altitude comp
    if scd30.altitude != config["altitude_ft"]:
        scd30.altitude = config["altitude_ft"]
    if scd30.temperature_offset != config["temp_offset_c"]:
        scd30.temperature_offset = config["temp_offset_c"]
    if scd30.measurement_interval != measurement_interval:
        scd30.measurement_interval = measurement_interval
    if config["co2_cal"]:
        scd30.forced_recalibration_reference = config["co2_cal"]

    # Start continous mode
    pressure = []
    for i in range(4):
        pressure.append(alarm.sleep_memory[i])
    scd30.ambient_pressure = struct.unpack("i", bytearray(pressure))[0]

    print("SCD30 Config:")
    print(f"altitude: {scd30.altitude}")
    print(f"measurement_interval: {scd30.measurement_interval}")
    print(f"temperature_offset: {scd30.temperature_offset}")
    print(f"ambient_pressure: {scd30.ambient_pressure}")
    print(f"co2_cal: {scd30.forced_recalibration_reference}")
    print(f"display_refresh_rate_sec: {config['display_refresh_rate_sec']}")
    print("")


def main() -> None:
    global display_time
    print("Time: %0.2f" % time.monotonic())

    # Initialization
    if not alarm.wake_alarm:
        # Initialize persisent data - right now just ambient pressure
        pressure = bytearray(struct.pack("i", config["ambient_pressure"]))
        for i, data in enumerate(pressure):
            alarm.sleep_memory[i] = data

    red_led = digitalio.DigitalInOut(board.D13)
    red_led.switch_to_output(value=False)

    magtag = MagTag()
    display = MagtagDisplay()

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
    network.connect()
    print("Time: %0.2f" % time.monotonic())

    # Create home assistant sensors
    def co2_read():
        while not scd30.data_available:
            time.sleep(0.5)
        return scd30.CO2

    sensor_battery = HomeAssistantSensor(
        SENSOR_NAME_BATTERY, lambda: magtag.peripherals.battery, 2, DeviceClass.BATTERY, "V")
    sensor_scd30_co2 = HomeAssistantSensor(
        SENSOR_NAME_SCD30_CO2, co2_read, 0, DeviceClass.CARBON_DIOXIDE, "ppm")
    sensor_scd30_hum = HomeAssistantSensor(
        SENSOR_NAME_SCD30_HUM, lambda: scd30.relative_humidity, 0, DeviceClass.HUMIDITY, "%")
    sensor_scd30_temp = HomeAssistantSensor(
        SENSOR_NAME_SCD30_TEMP, lambda: scd30.temperature, 1, DeviceClass.TEMPERATURE, "°C")

    # Create home assistant device
    co2_device = HomeAssistantDevice(DEVICE_NAME, "Magtag", mqtt_client)
    co2_device.add_sensor(sensor_scd30_co2)  # Read first since it has wait for data loop
    co2_device.add_sensor(sensor_scd30_hum)
    co2_device.add_sensor(sensor_scd30_temp)
    co2_device.add_sensor(sensor_battery)

    # Send home assistant mqtt discovery
    try:
        co2_device.send_discovery()
    except (ValueError, RuntimeError, MQTT.MMQTTException) as e:
        print("SCD30 MQTT Discovery failure, rebooting\n", e)
        reload()

    display_time = time.monotonic()

    # Main Loop
    while True:
        process(co2_device, display, network)

        if state_light_sleep:
            if state_light_sleep != runtime.serial_connected:
                reload()  # State transition, reboot into deep sleep state
            else:
                magtag.enter_light_sleep(config["light_sleep_sec"])
        else:
            # Change interval to large value - effectively turn off sampling while sleeping
            scd30.measurement_interval = SCD30_SAMPLE_RATE_SLOW_SEC

            network.disconnect()

            if state_light_sleep != runtime.serial_connected and config["force_deep_sleep"] is False:
                reload()  # State transition, reboot into light sleep state
            else:
                magtag.exit_and_deep_sleep(config["deep_sleep_sec"])


main()
