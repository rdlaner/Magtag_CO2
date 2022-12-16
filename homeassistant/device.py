import adafruit_minimqtt.adafruit_minimqtt as MQTT
import binascii
import json
import microcontroller
import os

from homeassistant import DISCOVERY_PREFIX
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
        self.topic = f"{DISCOVERY_PREFIX}/sensor/{self.device_id}"
        self.discovery_device = {
            "ids": f"{binascii.hexlify(microcontroller.cpu.uid).decode()}",
            "mf": f"{self.mf}",
            "mdl": f"{os.uname().machine}",
            "name": f"{self.device_name}",
            "sw": f"{os.uname().version}"
        }
        self.sensors = []
        self.sensor_data_cache = []

    def add_sensor(self, sensor: HomeAssistantSensor):
        if self.debug:
            print(f"Adding sensor: {sensor.sensor_name}")

        # Prepend device name to sensor name to further differentiate it
        sensor.set_device_name(
            f"{self.device_name}_{sensor.sensor_name.replace(' ', '_')}")

        # Update sensor discovery info with device details
        sensor_unique_id = f"{self.device_id}_{sensor.device_name}"
        sensor.set_discovery_topic(f"{DISCOVERY_PREFIX}/sensor/{sensor_unique_id}/config")
        sensor.set_discovery_info("~", self.topic)
        sensor.set_discovery_info("name", sensor.device_name)
        sensor.set_discovery_info("uniq_id", sensor_unique_id)
        sensor.set_discovery_info("dev", self.discovery_device)
        sensor.set_discovery_info(
            "val_tpl",
            f"{{{{ value_json.{sensor.device_name} | round({sensor.precision}) }}}}"
        )

        # Add to collection of device sensors
        self.sensors.append(sensor)

    def publish_sensors(self):
        """Publish all cached sensor data"""
        topic = f"{self.topic}/state"

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
                cached_data[sensor.device_name] = sensor_data

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
