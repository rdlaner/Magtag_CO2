import adafruit_minimqtt.adafruit_minimqtt as MQTT
import ipaddress
import time
import wifi

from adafruit_magtag.magtag import MagTag
from supervisor import reload


class MagtagNetwork():
    # Constants
    CONNECT_ATTEMPTS_WIFI = 10
    GOOGLE_IP_ADDRESS = ipaddress.ip_address("8.8.4.4")

    def __init__(self, magtag: MagTag, mqtt_client: MQTT.MQTT) -> None:
        self.magtag = magtag
        self.mqtt_client = mqtt_client

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

        if not self.magtag.network.is_connected:
            self.magtag.network.enabled = True

            try:
                self.magtag.network.connect(max_attempts=self.CONNECT_ATTEMPTS_WIFI)
            except (TypeError, OSError) as e:
                print(f"Failed to connect to Wifi! {e}")
                print("Rebooting...")
                reload()
            else:
                print(f"Wifi is connected: {wifi.radio.ipv4_address}")
        else:
            print("Wifi is already connected")

    def connect(self) -> None:
        print("Connecting network devices...")

        self._wifi_connect()
        self._mqtt_connect()

    def disconnect(self) -> None:
        print("Disconnecting network devices...")

        self._mqtt_disconnect()
        print(f"MQTT is connected: {self.mqtt_client.is_connected()}")

        self.magtag.network.enabled = False
        print(f"Wifi is connected: {self.magtag.network.is_connected}")

    def loop(self, recover: bool = False) -> None:
        try:
            self.mqtt_client.loop()
        except (BrokenPipeError, ValueError, RuntimeError, MQTT.MMQTTException) as e:
            print(f"MQTT loop failure\n{e}")
            if recover:
                self.recover()

    def recover(self) -> None:
        print("Recovering network devices...")

        if self.magtag.network.is_connected and not wifi.radio.ping(self.GOOGLE_IP_ADDRESS):
            # Reset wifi connection if ping is NOT successful
            self.magtag.network.enabled = False
            time.sleep(1)
            self.magtag.network.enabled = True

            self._wifi_connect()
        elif not self.magtag.network.is_connected:
            # Reconnect wifi
            self._wifi_connect()

        # MQTT reconnect attempt
        self._mqtt_disconnect(force=True)
        self._mqtt_connect(force=True)
