import alarm
import struct

from micropython import const

FREE_INDEX_OFFSET = const(0)
NUM_ELEMS_OFFSET = const(4)
ELEMENTS_OFFSET = const(8)
ELEMENT_BYTE_ORDER = ">"
ELEMENT_FORMAT = "BBs%ds%s"  # name_len, data_len, data_type, name, data type str
ELEMENT_FORMAT_STR = ELEMENT_BYTE_ORDER + ELEMENT_FORMAT
ELEMENT_NAME_LEN_OFFSET = const(0)
ELEMENT_DATA_LEN_OFFSET = const(1)
ELEMENT_DATA_TYPE_0FFSET = const(2)
ELEMENT_NAME_OFFSET = const(3)


class NonVolatileMemory():
    def __init__(self, reset: bool = False) -> None:
        if reset:
            self.reset()

        free_index = self._get_free_index()
        num_elements = self._get_num_elems()

        if free_index < ELEMENTS_OFFSET:
            self._set_free_index(ELEMENTS_OFFSET)

        self.elements = {}
        index = ELEMENTS_OFFSET
        for i in range(num_elements):
            name = self._element_get_name(index)
            size = self._element_get_size(index)
            self.elements[name] = index
            index += size

    def _get_free_index(self) -> int:
        return self._get_sleep_memory_data(
            FREE_INDEX_OFFSET, FREE_INDEX_OFFSET + 4, "i")

    def _get_num_elems(self) -> int:
        return self._get_sleep_memory_data(
            NUM_ELEMS_OFFSET, NUM_ELEMS_OFFSET + 4, "i")

    def _set_free_index(self, value) -> int:
        self._set_sleep_memory_data(FREE_INDEX_OFFSET, "i", value)

    def _set_num_elems(self, value) -> int:
        self._set_sleep_memory_data(NUM_ELEMS_OFFSET, "i", value)

    def _element_get_name_length(self, start_byte: int) -> int:
        byte_data = bytearray(
            [alarm.sleep_memory[start_byte + ELEMENT_NAME_LEN_OFFSET]])
        return struct.unpack_from(">B", byte_data, 0)[0]

    def _element_get_data_len(self, start_byte: int) -> int:
        byte_data = bytearray(
            [alarm.sleep_memory[start_byte + ELEMENT_DATA_LEN_OFFSET]])
        return struct.unpack_from(">B", byte_data, 0)[0]

    def _element_get_data_type(self, start_byte: int) -> str:
        byte_data = bytearray(
            [alarm.sleep_memory[start_byte + ELEMENT_DATA_TYPE_0FFSET]])
        return struct.unpack_from(">s", byte_data, 0)[0].decode()

    def _element_get_name(self, start_byte: int) -> str:
        name_len = self._element_get_name_length(start_byte)
        offset = start_byte +\
            struct.calcsize(ELEMENT_FORMAT[ELEMENT_NAME_LEN_OFFSET]) +\
            struct.calcsize(ELEMENT_FORMAT[ELEMENT_DATA_LEN_OFFSET]) +\
            struct.calcsize(ELEMENT_FORMAT[ELEMENT_DATA_TYPE_0FFSET])
        byte_data = bytearray()

        for i in range(name_len):
            byte_data.append(alarm.sleep_memory[offset + i])

        return struct.unpack(f">{name_len}s", byte_data)[0].decode()

    def _element_get_data(self, start_byte: int):
        name_len = self._element_get_name_length(start_byte)
        data_len = self._element_get_data_len(start_byte)
        data_type = self._element_get_data_type(start_byte)
        offset = start_byte +\
            struct.calcsize(ELEMENT_FORMAT[ELEMENT_NAME_LEN_OFFSET]) +\
            struct.calcsize(ELEMENT_FORMAT[ELEMENT_DATA_LEN_OFFSET]) +\
            struct.calcsize(ELEMENT_FORMAT[ELEMENT_DATA_TYPE_0FFSET]) +\
            name_len
        byte_data = bytearray()

        for i in range(data_len):
            byte_data.append(alarm.sleep_memory[offset + i])

        return struct.unpack(f">{data_type}", byte_data)[0]

    def _element_get_size(self, start_byte: int) -> int:
        name_len = self._element_get_name_length(start_byte)
        data_len = self._element_get_data_len(start_byte)
        size = struct.calcsize(ELEMENT_FORMAT[ELEMENT_NAME_LEN_OFFSET]) +\
            struct.calcsize(ELEMENT_FORMAT[ELEMENT_DATA_LEN_OFFSET]) +\
            struct.calcsize(ELEMENT_FORMAT[ELEMENT_DATA_TYPE_0FFSET]) +\
            name_len +\
            data_len
        return size

    def _get_sleep_memory_data(self, start_byte: int, end_byte: int, data_type: str):
        byte_data = bytearray()
        for i in range(start_byte, end_byte):
            byte_data.append(alarm.sleep_memory[i])

        return struct.unpack(f">{data_type}", byte_data)[0]

    def _set_sleep_memory_data(self, start_byte: int, data_type: str, data):
        byte_data = struct.pack(f">{data_type}", data)
        for i, byte in enumerate(byte_data):
            alarm.sleep_memory[start_byte + i] = byte

    def add_element(self, name: str, data_type: str, data) -> None:
        # Pack up the element: name_len, data_len, data_type, name, data
        packed_data = struct.pack(
            ELEMENT_FORMAT_STR % (len(name), data_type),
            len(name),
            struct.calcsize(data_type),
            data_type.encode(),
            name.encode(),
            data
        )

        # Append element to non-volatile memory bytearray
        index = self._get_free_index()
        num_elements = self._get_num_elems()
        self.elements[name] = index
        for byte in packed_data:
            alarm.sleep_memory[index] = byte
            index += 1

        # Update free index and num elements
        self._set_free_index(index)
        self._set_num_elems(num_elements + 1)

    def get_element(self, name: str):
        return self._element_get_data(self.elements[name])

    def print_elements(self):
        print(f"Free index: {self._get_free_index()}")
        print(f"Num elems: {self._get_num_elems()}")

        print("Non-volatile elements:")
        for name in self.elements.keys():
            print(f"{name}: {self.get_element(name)}")

    def reset(self):
        print("Reseting nv memory...")
        for i in range(len(alarm.sleep_memory)):
            alarm.sleep_memory[i] = 0

        self._set_free_index(ELEMENTS_OFFSET)
        self._set_num_elems(0)
        self.elements = {}

    def set_element(self, name: str, value):
        start_byte = self.elements[name]
        data_type = self._element_get_data_type(start_byte)

        # Rebuild packed struct w/ new value
        packed_data = struct.pack(
            ELEMENT_FORMAT_STR % (len(name), data_type),
            len(name),
            struct.calcsize(data_type),
            data_type.encode(),
            name.encode(),
            value
        )

        # Update element in memory
        for i, byte in enumerate(packed_data):
            alarm.sleep_memory[start_byte + i] = byte
