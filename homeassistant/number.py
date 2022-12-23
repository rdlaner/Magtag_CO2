from homeassistant import DISCOVERY_PREFIX


class HomeAssistantNumber():
    def __init__(self, name: str,
                 read_fxn,
                 precision: int = 0,
                 max_value: float = 100,
                 min_value: float = 0,
                 step: float = 1.0,
                 device_class: str = None,
                 mode: str = None,
                 unit: str = None) -> None:
        self.name = name
        self.read_fxn = read_fxn
        self.precision = precision
        self.device_name = ""

        # Default discovery info
        self.discovery_topic = f"{DISCOVERY_PREFIX}/number/{self.name.replace(' ', '_')}/config"
        self.discovery_info = {
            "~": f"{DISCOVERY_PREFIX}/number/{self.name.replace(' ', '_')}",
            "name": f"{name}",
            "stat_t": "~/state",
            "cmd_t": "~/cmd",
            "min": min_value,
            "max": max_value,
            "step": step,
            "cmd_tpl": f"{{ '{name}': {{{{ value }}}} }}"
        }
        if device_class:
            self.discovery_info["device_class"] = device_class
        if unit:
            self.discovery_info["unit_of_meas"] = unit
        if mode:
            self.discovery_info["mode"] = mode

    def read(self):
        return self.read_fxn()

    def set_device_name(self, name: str) -> None:
        self.device_name = name

    def set_discovery_info(self, key: str, value: str) -> None:
        self.discovery_info[key] = value

    def set_discovery_topic(self, topic: str) -> None:
        self.discovery_topic = topic
