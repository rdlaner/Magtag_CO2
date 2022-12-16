import board
import displayio
import terminalio
import time

from adafruit_display_text import label


class MagtagDisplay:
    # Constants
    CO2_PREFIX = "CO2:"
    CO2_SUFFIX = "ppm"
    TEMP_PREFIX = "T:"
    TEMP_SUFFIX = "F"
    HUM_PREFIX = "H:"
    HUM_SUFFIX = "%"
    BATT_PREFIX = "B:"
    BATT_SUFFIX = "V"

    def __init__(self) -> None:
        self.display = board.DISPLAY
        self.main_group = displayio.Group()
        self.co2_label = label.Label(
            font=terminalio.FONT, text=self.CO2_PREFIX, scale=3
        )
        self.temp_label = label.Label(
            font=terminalio.FONT, text=self.TEMP_PREFIX, scale=1
        )
        self.hum_label = label.Label(
            font=terminalio.FONT, text=self.HUM_PREFIX, scale=1
        )
        self.batt_label = label.Label(
            font=terminalio.FONT, text=self.BATT_PREFIX, scale=1
        )
        self.usb_label = label.Label(
            font=terminalio.FONT, text="USB:", scale=1
        )

        self.co2_label.anchor_point = (0.5, 0.0)
        self.co2_label.anchored_position = (self.display.width // 2, 10)
        self.temp_label.anchor_point = (0.5, 0)
        self.temp_label.anchored_position = (self.display.width // 6, 80)
        self.hum_label.anchor_point = (0.5, 0)
        self.hum_label.anchored_position = (3 * self.display.width // 6, 80)
        self.batt_label.anchor_point = (0.5, 0)
        self.batt_label.anchored_position = (5 * self.display.width // 6, 80)
        self.usb_label.anchor_point = (0.5, 0)
        self.usb_label.anchored_position = (3 * self.display.width // 6, 100)

        self.main_group.append(self.co2_label)
        self.main_group.append(self.temp_label)
        self.main_group.append(self.hum_label)
        self.main_group.append(self.batt_label)
        self.main_group.append(self.usb_label)
        self.display.show(self.main_group)

    def _build_text_batt(self, batt_val):
        return f"{self.BATT_PREFIX} {batt_val:.2f} {self.BATT_SUFFIX}"

    def _build_text_co2(self, co2_val):
        return f"{self.CO2_PREFIX} {co2_val:.0f} {self.CO2_SUFFIX}"

    def _build_text_hum(self, hum_val):
        return f"{self.HUM_PREFIX} {hum_val:.0f} {self.HUM_SUFFIX}"

    def _build_text_temp(self, temp_val):
        return f"{self.TEMP_PREFIX} {temp_val:.1f} {self.TEMP_SUFFIX}"

    def _build_text_usb(self, usb_val):
        return f"USB: {usb_val}"

    def refresh(self):
        time.sleep(self.display.time_to_refresh + 1)
        self.display.refresh()

    def update_batt(self, val):
        self.batt_label.text = self._build_text_batt(val)

    def update_co2(self, val):
        self.co2_label.text = self._build_text_co2(val)

    def update_hum(self, val):
        self.hum_label.text = self._build_text_hum(val)

    def update_temp(self, val):
        self.temp_label.text = self._build_text_temp(val)

    def update_usb(self, val):
        self.usb_label.text = self._build_text_usb(val)
