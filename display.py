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
        color_bitmap = displayio.Bitmap(self.display.width, self.display.height, 1)
        color_palette = displayio.Palette(1)
        color_palette[0] = 0xFFFFFF
        self.bg_sprite = displayio.TileGrid(
            color_bitmap,
            pixel_shader=color_palette,
            x=0,
            y=0
        )

        self.co2_label = label.Label(
            font=terminalio.FONT, text=self.CO2_PREFIX, scale=3, color=0
        )
        self.temp_label = label.Label(
            font=terminalio.FONT, text=self.TEMP_PREFIX, scale=1, color=0
        )
        self.hum_label = label.Label(
            font=terminalio.FONT, text=self.HUM_PREFIX, scale=1, color=0
        )
        self.batt_label = label.Label(
            font=terminalio.FONT, text=self.BATT_PREFIX, scale=1, color=0
        )
        self.datetime_label = label.Label(
            font=terminalio.FONT, text="", scale=1, color=0
        )

        self.co2_label.anchor_point = (0.5, 0.0)
        self.co2_label.anchored_position = (self.display.width // 2, 10)
        self.temp_label.anchor_point = (0.5, 0)
        self.temp_label.anchored_position = (self.display.width // 6, 80)
        self.hum_label.anchor_point = (0.5, 0)
        self.hum_label.anchored_position = (3 * self.display.width // 6, 80)
        self.batt_label.anchor_point = (0.5, 0)
        self.batt_label.anchored_position = (5 * self.display.width // 6, 80)
        self.datetime_label.anchor_point = (0.5, 0)
        self.datetime_label.anchored_position = (3 * self.display.width // 6, 100)

        self.main_group = displayio.Group()
        self.main_group.append(self.bg_sprite)
        self.main_group.append(self.co2_label)
        self.main_group.append(self.temp_label)
        self.main_group.append(self.hum_label)
        self.main_group.append(self.batt_label)
        self.main_group.append(self.datetime_label)
        self.display.show(self.main_group)

    def _build_text_batt(self, batt_val):
        try:
            text = f"{self.BATT_PREFIX} {batt_val:.2f} {self.BATT_SUFFIX}"
        except ValueError as e:
            text = None
            print(f"Invalid display value: {batt_val}")
            print(e)

        return text

    def _build_text_co2(self, co2_val):
        try:
            text = f"{self.CO2_PREFIX} {co2_val:.0f} {self.CO2_SUFFIX}"
        except ValueError as e:
            text = None
            print(f"Invalid display value: {co2_val}")
            print(e)

        return text

    def _build_text_hum(self, hum_val):
        try:
            text = f"{self.HUM_PREFIX} {hum_val:.0f} {self.HUM_SUFFIX}"
        except ValueError as e:
            text = None
            print(f"Invalid display value: {hum_val}")
            print(e)

        return text

    def _build_text_temp(self, temp_val):
        try:
            text = f"{self.TEMP_PREFIX} {temp_val:.1f} {self.TEMP_SUFFIX}"
        except ValueError as e:
            text = None
            print(f"Invalid display value: {temp_val}")
            print(e)

        return text

    def _build_text_datetime(self, datetime_val):
        try:
            text = f"{datetime_val}"
        except ValueError as e:
            text = None
            print(f"Invalid display value: {datetime_val}")
            print(e)

        return text

    def refresh(self, delay: bool = True):
        if delay:
            time.sleep(self.display.time_to_refresh + 1)
        self.display.refresh()

    def update_batt(self, val):
        text = self._build_text_batt(val)
        if text:
            self.batt_label.text = text

    def update_co2(self, val):
        text = self._build_text_co2(val)
        if text:
            self.co2_label.text = text

    def update_hum(self, val):
        text = self._build_text_hum(val)
        if text:
            self.hum_label.text = text

    def update_temp(self, val):
        text = self._build_text_temp(val)
        if text:
            self.temp_label.text = text

    def update_datetime(self, val):
        text = self._build_text_datetime(val)
        if text:
            self.datetime_label.text = text
