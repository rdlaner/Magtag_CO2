import adafruit_minimqtt.adafruit_minimqtt as MQTT
import adafruit_ntp
import ipaddress
import gc
import rtc
import time
import wifi

from secrets import secrets
from supervisor import reload


class MagtagNetwork():
    # Constants
    CONNECT_ATTEMPTS_WIFI = 5
    GOOGLE_IP_ADDRESS = ipaddress.ip_address("8.8.4.4")

    def __init__(self, mqtt_client: MQTT.MQTT, ntp: adafruit_ntp.NTP = None, hostname: str = None) -> None:
        self.mqtt_client = mqtt_client
        self.ntp = ntp
        self.wifi_connected = False

        wifi.radio.hostname = hostname
        self._wifi_disable()

    def _mqtt_connect(self, force: bool = False) -> None:
        print("Connecting MQTT client...")

        if not self.mqtt_client.is_connected() or force is True:
            try:
                self.mqtt_client.connect()
            except (OSError, ValueError, RuntimeError, MQTT.MMQTTException) as e:
                print(f"Failed to connect MQTT! {e}")
                print("Rebooting...")
                reload()
            else:
                print("MQTT is connected!")
        else:
            print("MQTT is already connected")

    def _mqtt_disconnect(self, force: bool = False) -> None:
        print("Disconnecting MQTT client...")

        if self.mqtt_client.is_connected() or force is True:
            try:
                self.mqtt_client.disconnect()
            except (OSError, RuntimeError, MQTT.MMQTTException) as e:
                print(f"Failed to disconnect MQTT! {e}")
            else:
                print("MQTT disconnected!")
        else:
            print("MQTT is already disconnected")

    def _wifi_connect(self) -> None:
        print("Connecting wifi...")

        if not self.wifi_connected:
            self._wifi_enable()
            ssid = secrets["ssid"]
            password = secrets["password"]
            attempt = 1

            while not self.wifi_connected:
                print(f"Connecting to AP: {ssid}")

                try:
                    wifi.radio.connect(ssid, password)
                except (RuntimeError, ConnectionError) as error:
                    print(f"Could not connect to internet: {error}")

                if not wifi.radio.ipv4_address:
                    self.wifi_connected = False
                    if attempt >= self.CONNECT_ATTEMPTS_WIFI:
                        break
                    print("Retrying in 3 seconds...")
                    attempt += 1
                    time.sleep(3)
                else:
                    self.wifi_connected = True

                gc.collect()

            if not self.wifi_connected:
                print("Failed to connect to Wifi!")
                print("Rebooting...")
                reload()
            else:
                print(f"Wifi is connected: {wifi.radio.ipv4_address}")
        else:
            print("Wifi is already connected")

    def _wifi_enable(self):
        wifi.radio.enabled = True

    def _wifi_disable(self):
        wifi.radio.enabled = False
        self.wifi_connected = False

    def connect(self) -> None:
        print("Connecting network devices...")

        self._wifi_connect()
        self._mqtt_connect()

    def disconnect(self) -> None:
        print("Disconnecting network devices...")

        self._mqtt_disconnect()
        print(f"MQTT is connected: {self.mqtt_client.is_connected()}")

        self._wifi_disable()
        print(f"Wifi is connected: {self.wifi_connected}")

    def is_connected(self) -> bool:
        return self.mqtt_client.is_connected()

    def loop(self, recover: bool = False) -> None:
        try:
            self.mqtt_client.loop()
        except (BrokenPipeError, ValueError, RuntimeError, MQTT.MMQTTException) as e:
            print(f"MQTT loop failure\n{e}")
            if recover:
                self.recover()

    def recover(self) -> None:
        print("Recovering network devices...")

        if self.wifi_connected and not wifi.radio.ping(self.GOOGLE_IP_ADDRESS):
            # Reset wifi connection if ping is NOT successful
            self._wifi_disable()
            time.sleep(1)
            self._wifi_enable()

            self._wifi_connect()
        elif not self.wifi_connected:
            # Reconnect wifi
            self._wifi_connect()

        # MQTT reconnect attempt
        self._mqtt_disconnect(force=True)
        self._mqtt_connect(force=True)

    def ntp_time_sync(self) -> bool:
        if not self.ntp:
            print("NTP time sync failed, no ntp object created.")
            return False

        result = True
        try:
            rtc.RTC().datetime = self.ntp.datetime
        except OSError as e:
            print("NTP time sync failed!")
            print(e)
            result = False

        return result
