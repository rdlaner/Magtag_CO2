import adafruit_minimqtt.adafruit_minimqtt as MQTT
import adafruit_ntp
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
from memory import BackupRAM
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
# TODO: Add last read time to display

# Constants
MQTT_CONNECT_ATTEMPTS = const(10)
MQTT_RX_TIMEOUT_SEC = const(10)
MQTT_KEEP_ALIVE_MARGIN_SEC = const(20)
FORCE_CAL_DISABLED = const(-1)
TZ_OFFSET_PACIFIC = const(-8)
MAGTAG_BATT_DXN_VOLTAGE = 4.20
DEVICE_NAME = "Test"
SENSOR_NAME_BATTERY = "Batt Voltage"
NUMBER_NAME_TEMP_OFFSET = "Temp Offset"
NUMBER_NAME_PRESSURE = "Pressure"
NUMBER_NAME_CO2_REF = "CO2 Ref"
BACKUP_NAME_PRESSURE = "pressure"
BACKUP_NAME_CAL = "forced cal"
BACKUP_NAME_TEMP_OFFSET = "temp offset"
BACKUP_NAME_DISPLAY_TIME = "display"
BACKUP_NAME_TIME_SYNC_TIME = "time sync"
BACKUP_NAME_UPLOAD_TIME = "upload"
TIME_FMT_STR = "%d:%02d:%02d"
DATA_FMT_STR = "%d/%d/%d"

# Globals
state_light_sleep = runtime.serial_connected if not config["force_deep_sleep"] else False
backup_ram = BackupRAM()


def c_to_f(temp_cels: float) -> float:
    return (temp_cels * 1.8) + 32.0 if temp_cels else None


def get_fmt_time(epoch_time: int = None) -> str:
    if epoch_time:
        now = time.localtime(epoch_time)
    else:
        now = time.localtime()
    return TIME_FMT_STR % (now[3], now[4], now[5])


def get_fmt_date(epoch_time: int = None) -> str:
    if epoch_time:
        now = time.localtime(epoch_time)
    else:
        now = time.localtime()
    return DATA_FMT_STR % (now[1], now[2], now[0])


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
            print(f"Ambient pressure value invalid\n{e}")
            return

        print(f"Updating backup pressure to {pressure}")
        backup_ram.set_element(BACKUP_NAME_PRESSURE, pressure)

    elif topic == config["cmd_topic"]:
        try:
            obj = json.loads(message)
        except ValueError as e:
            print(f"Forced calibration value invalid\n{e}")
            return

        if NUMBER_NAME_CO2_REF in obj:
            cal_val = obj[NUMBER_NAME_CO2_REF]
            print(f"Updating backup cal to {cal_val}")
            backup_ram.set_element(BACKUP_NAME_CAL, cal_val)

        if NUMBER_NAME_TEMP_OFFSET in obj:
            temp_offset = obj[NUMBER_NAME_TEMP_OFFSET]
            print(f"Updating backup temp offset to {temp_offset}")
            backup_ram.set_element(BACKUP_NAME_TEMP_OFFSET, temp_offset)


def main() -> None:
    print("\nInitializing...")
    print(f"Time: {time.time()}")

    first_boot = not alarm.wake_alarm
    if first_boot:
        # Initialize persistent data
        backup_ram.reset()
        backup_ram.add_element(
            BACKUP_NAME_PRESSURE, "I", config["ambient_pressure"])
        backup_ram.add_element(
            BACKUP_NAME_CAL, "i", FORCE_CAL_DISABLED)
        backup_ram.add_element(
            BACKUP_NAME_TEMP_OFFSET, "f", config["temp_offset_c"]
        )

    red_led = digitalio.DigitalInOut(board.D13)
    red_led.switch_to_output(value=False)

    magtag = MagTag()
    magtag.peripherals.neopixel_disable = True
    magtag.peripherals.speaker_disable = True

    display = MagtagDisplay()

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
    ntp = adafruit_ntp.NTP(socket_pool, tz_offset=TZ_OFFSET_PACIFIC)
    network = MagtagNetwork(magtag, mqtt_client, ntp)

    def read_batt():
        volts = magtag.peripherals.battery
        return volts if volts < MAGTAG_BATT_DXN_VOLTAGE else 0

    # Create home assistant sensors
    sensor_battery = HomeAssistantSensor(
        SENSOR_NAME_BATTERY, read_batt, 2, DeviceClass.BATTERY, "V")

    # Create home assistant numbers
    number_temp_offset = HomeAssistantNumber(
        NUMBER_NAME_TEMP_OFFSET,
        lambda: backup_ram.get_element(BACKUP_NAME_TEMP_OFFSET),
        precision=1,
        unit="°C",
        min_value=0,
        mode="box")
    number_pressure = HomeAssistantNumber(
        NUMBER_NAME_PRESSURE,
        lambda: backup_ram.get_element(BACKUP_NAME_PRESSURE),
        device_class=DeviceClass.PRESSURE,
        unit="mbar",
        min_value=100,
        max_value=1100,
        mode="box")
    number_co2_ref = HomeAssistantNumber(
        NUMBER_NAME_CO2_REF,
        lambda: backup_ram.get_element(BACKUP_NAME_CAL),
        device_class=DeviceClass.CARBON_DIOXIDE,
        unit="ppm",
        min_value=400,
        max_value=5000,
        mode="box")

    # Create home assistant device
    co2_device = HomeAssistantDevice(DEVICE_NAME, "Magtag", mqtt_client)
    co2_device.add_sensor(sensor_battery)
    co2_device.add_number(number_temp_offset)
    co2_device.add_number(number_pressure)
    co2_device.add_number(number_co2_ref)

    # Set command topic
    config["cmd_topic"] = f"{co2_device.number_topic}/cmd"

    # Time sync on first boot
    if first_boot:
        network.connect()
        network.ntp_time_sync()

        now = time.time()
        backup_ram.add_element(BACKUP_NAME_DISPLAY_TIME, "I", now)
        backup_ram.add_element(BACKUP_NAME_TIME_SYNC_TIME, "I", now)
        backup_ram.add_element(BACKUP_NAME_UPLOAD_TIME, "I", now)

    backup_ram.print_elements()
    print(f"Time: {time.time()}")
    print("")

    # Main Loop
    while True:
        print("Processing...")
        print(f"Time: {time.time()}")

        # Load backup RAM data
        display_time = backup_ram.get_element(BACKUP_NAME_DISPLAY_TIME)
        time_sync_time = backup_ram.get_element(BACKUP_NAME_TIME_SYNC_TIME)
        upload_time = backup_ram.get_element(BACKUP_NAME_UPLOAD_TIME)
        current_pressure = backup_ram.get_element(BACKUP_NAME_PRESSURE)
        current_temp_offset = backup_ram.get_element(BACKUP_NAME_TEMP_OFFSET)
        current_cal_val = backup_ram.get_element(BACKUP_NAME_CAL)

        print("Reading sensors...")
        sensor_data = co2_device.read_sensors(cache=True)
        print(sensor_data)
        print(f"Time: {time.time()}")

        # Time sync
        if (time.time() - time_sync_time) >= config["time_sync_rate_sec"] or first_boot:
            print("Time syncing...")
            network.connect()
            if network.ntp_time_sync():
                backup_ram.set_element(BACKUP_NAME_TIME_SYNC_TIME, time.time())
                print(f"Time: {get_fmt_time()}")
                print(f"Data: {get_fmt_date()}")

        # Upload data
        if (time.time() - upload_time) >= config["upload_rate_sec"] or first_boot or state_light_sleep:
            print("Uploading data...")
            network.connect()

            # Send home assistant mqtt discovery
            try:
                co2_device.send_discovery()
            except (ValueError, RuntimeError, MQTT.MMQTTException) as e:
                print(f"CO2 device MQTT discovery failure, rebooting\n{e}")
                reload()

            # Service MQTT
            print(f"Time: {time.time()}")
            network.loop(recover=state_light_sleep)

            # Publish data to MQTT
            print(f"Time: {time.time()}")
            try:
                print("Publishing MQTT data...")
                co2_device.publish_numbers()
                co2_device.publish_sensors()
            except (OSError, ValueError, RuntimeError, MQTT.MMQTTException) as e:
                print(f"MQTT Publish failure\n{e}")
                if state_light_sleep:
                    network.recover()

            backup_ram.set_element(BACKUP_NAME_UPLOAD_TIME, time.time())

        # Turn off network if in deep sleep mode
        if not state_light_sleep and network.is_connected():
            network.disconnect()

        # Perform forced recal if received new cal value
        expected_cal_val = backup_ram.get_element(BACKUP_NAME_CAL)
        if expected_cal_val != FORCE_CAL_DISABLED and expected_cal_val != current_cal_val:
            print(f"Updating cal reference from {current_cal_val} to {expected_cal_val}")

        # Update temp offset if received a new value
        expected_temp_offset = backup_ram.get_element(BACKUP_NAME_TEMP_OFFSET)
        if expected_temp_offset != current_temp_offset:
            print(f"Updating temp offset from {current_temp_offset} to {expected_temp_offset}")

        # Update pressure if received a new value
        expected_pressure = backup_ram.get_element(BACKUP_NAME_PRESSURE)
        if expected_pressure != current_pressure:
            print(f"Updating pressure from {current_pressure} to {expected_pressure}")

        # Update display
        if ((time.time() - display_time) >= config["display_refresh_rate_sec"]) or not state_light_sleep:
            print(f"Time: {time.time()}")
            print("Updating display...")
            now = get_fmt_time()
            uploaded_time = get_fmt_time(backup_ram.get_element(BACKUP_NAME_UPLOAD_TIME))
            display.update_batt(sensor_data[SENSOR_NAME_BATTERY])
            display.update_datetime(f"Updated: {now}. Uploaded: {uploaded_time}")
            display.refresh(delay=False)
            backup_ram.set_element(BACKUP_NAME_DISPLAY_TIME, time.time())

        print(f"Time: {time.time()}")
        print("")

        print("Sleeping...")
        print("")
        if state_light_sleep:
            if state_light_sleep != runtime.serial_connected:
                reload()  # State transition, reboot into deep sleep state
            else:
                magtag.enter_light_sleep(config["light_sleep_sec"])
        else:
            if state_light_sleep != runtime.serial_connected and config["force_deep_sleep"] is False:
                reload()  # State transition, reboot into light sleep state
            else:
                magtag.exit_and_deep_sleep(config["deep_sleep_sec"])


main()
