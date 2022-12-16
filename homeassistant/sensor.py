from homeassistant import DISCOVERY_PREFIX


class HomeAssistantSensor():
    def __init__(self,
                 sensor_name: str,
                 read_fxn,
                 precision: int = 0,
                 device_class: str = None,
                 unit: str = None) -> None:
        self.sensor_name = sensor_name
        self.read_fxn = read_fxn
        self.precision = precision
        self.device_name = ""

        # Default discovery info
        self.discovery_topic = f"{DISCOVERY_PREFIX}/sensor/{self.sensor_name.replace(' ', '_')}/config"
        self.discovery_info = {
            "~": f"{DISCOVERY_PREFIX}/sensor/{self.sensor_name.replace(' ', '_')}",
            "name": f"{sensor_name}",
            "stat_t": "~/state"
        }
        if device_class:
            self.discovery_info["device_class"] = device_class
        if unit:
            self.discovery_info["unit_of_meas"] = unit

    def read(self):
        return self.read_fxn()

    def set_device_name(self, name: str) -> None:
        self.device_name = name

    def set_discovery_info(self, key: str, value: str) -> None:
        self.discovery_info[key] = value

    def set_discovery_topic(self, topic: str) -> None:
        self.discovery_topic = topic
