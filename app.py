import time
print(f"Time start: {time.monotonic()}")

import adafruit_minimqtt.adafruit_minimqtt as MQTT
import adafruit_ntp
import adafruit_scd4x
import alarm
import binascii
import digitalio
import board
import busio
import gc
import json
import microcontroller
import socketpool
import ssl
import wifi

print(f"Time imports: {time.monotonic()}")

from config import config
from display import MagtagDisplay
from homeassistant.device import HomeAssistantDevice
from homeassistant.number import HomeAssistantNumber
from homeassistant.sensor import HomeAssistantSensor
from homeassistant.device_class import DeviceClass
from memory import BackupRAM
from micropython import const
from network import MagtagNetwork
from peripherals import Peripherals
from secrets import secrets
from supervisor import runtime, reload, RunReason

print(f"Time from imports: {time.monotonic()}")

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
TZ_OFFSET_PACIFIC = const(-8)
SCD41_CMD_SINGLE_SHOT = const(0x219D)
SINGLE_SHOT_SLEEP_SEC = const(5)
SENSOR_READ_TIMEOUT_SEC = const(5)
STATE_LIGHT_SLEEP = const(0)
STATE_DEEP_SLEEP_SAMPLING = const(1)
STATE_DEEP_SLEEP = const(2)
MAGTAG_BATT_DXN_VOLTAGE = 4.20
DEVICE_NAME = "Magtag_CO2_SCD41"
SENSOR_NAME_BATTERY = "Batt Voltage"
SENSOR_NAME_SCD41_CO2 = "SCD41 CO2"
SENSOR_NAME_SCD41_HUM = "SCD41 Humidity"
SENSOR_NAME_SCD41_TEMP = "SCD41 Temperature"
NUMBER_NAME_TEMP_OFFSET = "Temp Offset"
NUMBER_NAME_PRESSURE = "Pressure"
NUMBER_NAME_CO2_REF = "CO2 Ref"
BACKUP_NAME_PRESSURE = "pressure"
BACKUP_NAME_CAL = "forced cal"
BACKUP_NAME_TEMP_OFFSET = "temp offset"
BACKUP_NAME_DISPLAY_TIME = "display"
BACKUP_NAME_TIME_SYNC_TIME = "time sync"
BACKUP_NAME_UPLOAD_TIME = "upload"
BACKUP_NAME_STATE = "state"
BACKUP_NAME_FIRST_TIME_SYNC = "first sync"
TIME_FMT_STR = "%d:%02d:%02d"
DATA_FMT_STR = "%d/%d/%d"

# Globals
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


def deep_sleep(sleep_time: float) -> None:
    time_alarm = alarm.time.TimeAlarm(monotonic_time=time.monotonic() + sleep_time)
    alarm.exit_and_deep_sleep_until_alarms(time_alarm)


def light_sleep(sleep_time: float) -> None:
    time_alarm = alarm.time.TimeAlarm(monotonic_time=time.monotonic() + sleep_time)
    alarm.light_sleep_until_alarms(time_alarm)
    gc.collect()


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


def process_calibration(scd41: adafruit_scd4x, current_cal_val: int) -> bool:
    """Perform forced recal if received new cal value"""
    updated = False
    expected_cal_val = backup_ram.get_element(BACKUP_NAME_CAL)

    if expected_cal_val != FORCE_CAL_DISABLED and expected_cal_val != current_cal_val:
        print(f"Updating cal reference from {current_cal_val} to {expected_cal_val}")
        scd41.force_calibration(expected_cal_val)
        updated = True

    return updated


def process_pressure(scd41: adafruit_scd4x, current_pressure: int) -> bool:
    """Update pressure if received a new value"""
    updated = False
    expected_pressure = backup_ram.get_element(BACKUP_NAME_PRESSURE)

    if expected_pressure != current_pressure:
        print(f"Updating pressure from {current_pressure} to {expected_pressure}")
        scd41.set_ambient_pressure(expected_pressure)
        updated = True

    return updated


def process_temp_offset(scd41: adafruit_scd4x, current_temp_offset: float) -> bool:
    """Update temp offset if received a new value"""
    updated = False
    expected_temp_offset = backup_ram.get_element(BACKUP_NAME_TEMP_OFFSET)

    if expected_temp_offset != current_temp_offset:
        print(f"Updating temp offset from {current_temp_offset} to {expected_temp_offset}")
        scd41.stop_periodic_measurement()
        scd41.temperature_offset = expected_temp_offset
        updated = True

    return updated


def scd41_init(scd41: adafruit_scd4x.SCD4X) -> None:
    # Only stop measurements and perform initializations once in order to prevent
    # unnecessary latency in deep sleep mode
    print("scd41 Config:")
    # Measurements must be stopped to make config updates
    scd41.stop_periodic_measurement()

    print("Updating self cal mode to OFF")
    scd41.self_calibration_enabled = False

    temp_offset = backup_ram.get_element(BACKUP_NAME_TEMP_OFFSET)
    print(f"Updating temperature offset to {temp_offset}")
    scd41.temperature_offset = temp_offset

    pressure = backup_ram.get_element(BACKUP_NAME_PRESSURE)
    print(f"Updating pressure to {pressure}")
    scd41.set_ambient_pressure(pressure)

    print("Persisting settings to EEPROM")
    scd41.persist_settings()


def time_sync(time_sync_time: int, network: MagtagNetwork):
    if (time.time() - time_sync_time) >= config["time_sync_rate_sec"]:
        print("Time syncing...")
        if not network.is_connected():
            network.connect()

        if network.ntp_time_sync():
            backup_ram.set_element(BACKUP_NAME_TIME_SYNC_TIME, time.time())
            print(f"Time: {get_fmt_time()}")
            print(f"Date: {get_fmt_date()}")


def update_display(display_time: int, display, sensor_data: list):
    if ((time.time() - display_time) >= config["display_refresh_rate_sec"]):
        print(f"Time mono: {time.monotonic()}")
        print("Updating display...")
        now = get_fmt_time()
        uploaded_time = get_fmt_time(backup_ram.get_element(BACKUP_NAME_UPLOAD_TIME))
        display.update_co2(sensor_data[SENSOR_NAME_SCD41_CO2])
        display.update_temp(c_to_f(sensor_data[SENSOR_NAME_SCD41_TEMP]))
        display.update_hum(sensor_data[SENSOR_NAME_SCD41_HUM])
        display.update_batt(sensor_data[SENSOR_NAME_BATTERY])
        display.update_datetime(f"Updated: {now}. Uploaded: {uploaded_time}")
        display.refresh(delay=False)
        backup_ram.set_element(BACKUP_NAME_DISPLAY_TIME, time.time())


def update_state(new_state: int):
    backup_ram.set_element(BACKUP_NAME_STATE, new_state)


def upload_data(upload_time: int, co2_device: HomeAssistantDevice, network: MagtagNetwork, recover: bool = False):
    if (time.time() - upload_time) >= config["upload_rate_sec"]:
        print("Uploading data...")
        if not network.is_connected():
            network.connect()

        # Send home assistant mqtt discovery
        try:
            co2_device.send_discovery()
        except (ValueError, RuntimeError, MQTT.MMQTTException) as e:
            print(f"CO2 device MQTT discovery failure, rebooting\n{e}")
            reload()

        # Service MQTT
        print(f"Time mono: {time.monotonic()}")
        network.loop(recover=recover)

        # Publish data to MQTT
        print(f"Time mono: {time.monotonic()}")
        try:
            print("Publishing MQTT data...")
            co2_device.publish_numbers()
            co2_device.publish_sensors()
        except (OSError, ValueError, RuntimeError, MQTT.MMQTTException) as e:
            print(f"MQTT Publish failure\n{e}")
            if recover:
                network.recover()

        backup_ram.set_element(BACKUP_NAME_UPLOAD_TIME, time.time())


def main() -> None:
    print(f"Time main: {time.monotonic()}")
    if runtime.run_reason == RunReason.SUPERVISOR_RELOAD:
        first_boot = False
    else:
        first_boot = not alarm.wake_alarm

    print(f"Reset reason: {microcontroller.cpu.reset_reason}")
    print(f"Run reason: {runtime.run_reason}")
    print(f"First boot: {first_boot}")
    print("\nInitializing...")

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
        backup_ram.add_element(
            BACKUP_NAME_FIRST_TIME_SYNC, "B", True
        )

        if runtime.serial_connected and not config["force_deep_sleep"]:
            initial_state = STATE_LIGHT_SLEEP
        else:
            initial_state = STATE_DEEP_SLEEP_SAMPLING

        backup_ram.add_element(
            BACKUP_NAME_STATE, "I", initial_state
        )

    # Initialize critical components first
    device_state = backup_ram.get_element(BACKUP_NAME_STATE)
    i2c = busio.I2C(board.SCL, board.SDA, frequency=100000)
    scd41 = adafruit_scd4x.SCD4X(i2c)
    if first_boot:
        scd41_init(scd41)
    if device_state == STATE_LIGHT_SLEEP:
        scd41.start_periodic_measurement()  # High performance mode

    # If in STATE_DEEP_SLEEP_SAMPLING, we don't need to take up time with the
    # rest of initialization. So, handle STATE_DEEP_SLEEP_SAMPLING here.
    if device_state == STATE_DEEP_SLEEP_SAMPLING:
        # Send single shot command and then go to sleep
        print("Sending single shot...")
        scd41._send_command(SCD41_CMD_SINGLE_SHOT)

        print(f"Time mono: {time.monotonic()}\n")
        print("Sleeping...\n")
        update_state(STATE_DEEP_SLEEP)
        deep_sleep(SINGLE_SHOT_SLEEP_SEC)

    # Init Magtag device and display
    red_led = digitalio.DigitalInOut(board.D13)
    red_led.switch_to_output(value=False)
    magtag_periphs = Peripherals()
    magtag_periphs.neopixel_disable = True
    magtag_periphs.speaker_disable = True
    display = MagtagDisplay()

    # Init network devices
    client_id = f"{DEVICE_NAME}-{binascii.hexlify(microcontroller.cpu.uid).decode()}"
    socket_pool = socketpool.SocketPool(wifi.radio)
    if device_state == STATE_LIGHT_SLEEP:
        keep_alive_sec = config["light_sleep_sec"] + MQTT_KEEP_ALIVE_MARGIN_SEC
    else:
        keep_alive_sec = config["deep_sleep_sec"] + MQTT_KEEP_ALIVE_MARGIN_SEC
    mqtt_client = MQTT.MQTT(
        client_id=client_id,
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
    network = MagtagNetwork(mqtt_client, ntp=ntp, hostname=client_id)

    # Sensor read functions
    def read_co2():
        start = time.monotonic()
        while not scd41.data_ready and time.monotonic() - start < SENSOR_READ_TIMEOUT_SEC:
            time.sleep(0.1)
        return scd41.CO2

    def read_batt():
        volts = magtag_periphs.battery
        return volts if volts < MAGTAG_BATT_DXN_VOLTAGE else 0

    # Create home assistant sensors
    sensor_battery = HomeAssistantSensor(
        SENSOR_NAME_BATTERY, read_batt, 2, DeviceClass.BATTERY, "V")
    sensor_scd41_co2 = HomeAssistantSensor(
        SENSOR_NAME_SCD41_CO2, read_co2, 0, DeviceClass.CARBON_DIOXIDE, "ppm")
    sensor_scd41_hum = HomeAssistantSensor(
        SENSOR_NAME_SCD41_HUM, lambda: scd41.relative_humidity, 0, DeviceClass.HUMIDITY, "%")
    sensor_scd41_temp = HomeAssistantSensor(
        SENSOR_NAME_SCD41_TEMP, lambda: scd41.temperature, 1, DeviceClass.TEMPERATURE, "°C")

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
    co2_device.add_sensor(sensor_scd41_co2)  # Add first
    co2_device.add_sensor(sensor_scd41_hum)
    co2_device.add_sensor(sensor_scd41_temp)
    co2_device.add_sensor(sensor_battery)
    co2_device.add_number(number_temp_offset)
    co2_device.add_number(number_pressure)
    co2_device.add_number(number_co2_ref)

    # Set command topic
    config["cmd_topic"] = f"{co2_device.number_topic}/cmd"

    # Time sync before first time through main loop
    first_sync = backup_ram.get_element(BACKUP_NAME_FIRST_TIME_SYNC)
    if first_sync:
        network.connect()
        network.ntp_time_sync()
        now = time.time()

        backup_ram.add_element(BACKUP_NAME_DISPLAY_TIME, "I", now)
        backup_ram.add_element(BACKUP_NAME_TIME_SYNC_TIME, "I", now)
        backup_ram.add_element(BACKUP_NAME_UPLOAD_TIME, "I", now)
        backup_ram.set_element(BACKUP_NAME_FIRST_TIME_SYNC, False)

    backup_ram.print_elements()
    print(f"Time mono: {time.monotonic()}")
    print("")

    # Main Loop
    while True:
        print("Processing...")
        print(f"Time mono: {time.monotonic()}")

        # Load backup RAM data
        display_time = backup_ram.get_element(BACKUP_NAME_DISPLAY_TIME)
        time_sync_time = backup_ram.get_element(BACKUP_NAME_TIME_SYNC_TIME)
        upload_time = backup_ram.get_element(BACKUP_NAME_UPLOAD_TIME)
        current_pressure = backup_ram.get_element(BACKUP_NAME_PRESSURE)
        current_temp_offset = backup_ram.get_element(BACKUP_NAME_TEMP_OFFSET)
        current_cal_val = backup_ram.get_element(BACKUP_NAME_CAL)

        # State Machine
        if device_state == STATE_LIGHT_SLEEP:
            print("Reading sensors...")
            sensor_data = co2_device.read_sensors(cache=True)
            print(sensor_data)
            print(f"Time mono: {time.monotonic()}")

            time_sync(time_sync_time, network)

            config["upload_rate_sec"] = config["light_sleep_sec"]
            upload_data(upload_time, co2_device, network, recover=True)

            if process_calibration(scd41, current_cal_val):
                scd41.start_periodic_measurement()

            if process_temp_offset(scd41, current_temp_offset):
                scd41.start_periodic_measurement()

            process_pressure(scd41, current_pressure)

            update_display(display_time, display, sensor_data)

            print(f"Time mono: {time.monotonic()}\n")
            if not runtime.serial_connected or config["force_deep_sleep"]:
                # State transition, reboot into deep sleep state
                update_state(STATE_DEEP_SLEEP_SAMPLING)
                print("Updated sleep should be deep sleep")
                backup_ram.print_elements()
                reload()
            else:
                print("Sleeping...\n")
                light_sleep(config["light_sleep_sec"])

        elif device_state == STATE_DEEP_SLEEP:
            print("Reading sensors...")
            sensor_data = co2_device.read_sensors(cache=True)
            print(sensor_data)
            print(f"Time mono: {time.monotonic()}")

            time_sync(time_sync_time, network)

            upload_data(upload_time, co2_device, network)

            process_calibration(scd41, current_cal_val)

            process_temp_offset(scd41, current_temp_offset)

            process_pressure(scd41, current_pressure)

            update_display(display_time, display, sensor_data)

            if network.is_connected():
                network.disconnect()

            print(f"Time mono: {time.monotonic()}\n")
            if runtime.serial_connected and not config["force_deep_sleep"]:
                # State transition, reboot into light sleep state
                update_state(STATE_LIGHT_SLEEP)
                reload()
            else:
                print("Sleeping...\n")
                update_state(STATE_DEEP_SLEEP_SAMPLING)
                deep_sleep(config["deep_sleep_sec"])
        else:
            raise Exception(f"Unknown state: {device_state}")


main()
