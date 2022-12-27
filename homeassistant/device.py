import adafruit_minimqtt.adafruit_minimqtt as MQTT
import binascii
import json
import microcontroller
import os

from homeassistant import DISCOVERY_PREFIX
from homeassistant.number import HomeAssistantNumber
from homeassistant.sensor import HomeAssistantSensor
from sys import platform


class HomeAssistantDevice():
    def __init__(self, device_name: str,
                 model: str,
                 mqtt_client: MQTT.MQTT,
                 debug: bool = False) -> None:
        self.device_name = device_name
        self.model = model
        self.mqtt_client = mqtt_client
        self.debug = debug
        self.mf = platform
        self.device_id = f"{self.model}-{binascii.hexlify(microcontroller.cpu.uid).decode()}"
        self.number_topic = f"{DISCOVERY_PREFIX}/number/{self.device_id}"
        self.sensor_topic = f"{DISCOVERY_PREFIX}/sensor/{self.device_id}"
        self.discovery_device = {
            "ids": f"{binascii.hexlify(microcontroller.cpu.uid).decode()}",
            "mf": f"{self.mf}",
            "mdl": f"{os.uname().machine}",
            "name": f"{self.device_name}",
            "sw": f"{os.uname().version}"
        }
        self.numbers = []
        self.sensors = []
        self.sensor_data_cache = []

    def add_number(self, number: HomeAssistantNumber):
        if self.debug:
            print(f"Adding number: {number.name}")

        # Prepend device name to sensor name to further differentiate it
        number.set_device_name(
            f"{self.device_name}_{number.name.replace(' ', '_')}")

        # Update sensor discovery info with device details
        number_unique_id = f"{self.device_id}_{number.device_name}"
        number.set_discovery_topic(f"{DISCOVERY_PREFIX}/number/{number_unique_id}/config")
        number.set_discovery_info("~", self.number_topic)
        number.set_discovery_info("obj_id", number.device_name)
        number.set_discovery_info("uniq_id", number_unique_id)
        number.set_discovery_info("dev", self.discovery_device)
        number.set_discovery_info(
            "val_tpl",
            f"{{{{ value_json.{number.name.replace(' ', '_')} | round({number.precision}) }}}}"
        )

        # Add to collection of device numbers
        self.numbers.append(number)

    def add_sensor(self, sensor: HomeAssistantSensor):
        if self.debug:
            print(f"Adding sensor: {sensor.sensor_name}")

        # Prepend device name to sensor name to further differentiate it
        sensor.set_device_name(
            f"{self.device_name}_{sensor.sensor_name.replace(' ', '_')}")

        # Update sensor discovery info with device details
        sensor_unique_id = f"{self.device_id}_{sensor.device_name}"
        sensor.set_discovery_topic(f"{DISCOVERY_PREFIX}/sensor/{sensor_unique_id}/config")
        sensor.set_discovery_info("~", self.sensor_topic)
        sensor.set_discovery_info("obj_id", sensor.device_name)
        sensor.set_discovery_info("stat_cla", "measurement")
        sensor.set_discovery_info("uniq_id", sensor_unique_id)
        sensor.set_discovery_info("dev", self.discovery_device)
        sensor.set_discovery_info(
            "val_tpl",
            f"{{{{ value_json.{sensor.sensor_name.replace(' ', '_')} | round({sensor.precision}) }}}}"
        )

        # Add to collection of device sensors
        self.sensors.append(sensor)

    def publish_numbers(self) -> None:
        """Publish all number data"""
        topic = f"{self.number_topic}/state"

        msg = {}
        for number in self.numbers:
            number_data = number.read()
            msg[number.name.replace(' ', '_')] = number_data

        if self.debug:
            print(f"Publishing to {topic}:")
            print(f"{json.dumps(msg)}")

        self.mqtt_client.publish(topic, json.dumps(msg), retain=True, qos=1)

    def publish_sensors(self) -> None:
        """Publish all cached sensor data"""
        topic = f"{self.sensor_topic}/state"

        for msg in self.sensor_data_cache:
            if self.debug:
                print(f"Publishing to {topic}:")
                print(f"{json.dumps(msg)}")

            self.mqtt_client.publish(topic, json.dumps(msg), retain=True, qos=1)

        self.sensor_data_cache = []

    def read_sensors(self, cache: bool = True) -> dict:
        """Read data from all added sensors.
           Data will be saved for publishing if cache == True"""
        data = {}
        cached_data = {}
        for sensor in self.sensors:
            sensor_data = sensor.read()
            data[sensor.sensor_name] = sensor_data
            if cache:
                cached_data[sensor.sensor_name.replace(' ', '_')] = sensor_data

        if cache:
            self.sensor_data_cache.append(cached_data)

        return data

    def send_discovery(self):
        """Send discovery data to Home Assistant"""
        for sensor in self.sensors:
            if self.debug:
                print(f"Discovery topic: {sensor.discovery_topic}")
                print(f"Discovery msg:\n{json.dumps(sensor.discovery_info)}")

            self.mqtt_client.publish(
                sensor.discovery_topic, json.dumps(sensor.discovery_info), retain=True, qos=1)

        for number in self.numbers:
            if self.debug:
                print(f"Discovery topic: {number.discovery_topic}")
                print(f"Discovery msg:\n{json.dumps(number.discovery_info)}")

            self.mqtt_client.publish(
                number.discovery_topic, json.dumps(number.discovery_info), retain=True, qos=1)
