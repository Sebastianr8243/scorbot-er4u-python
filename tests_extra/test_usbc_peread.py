"""tools/usbc_analysis/peread.py against a tiny hand-built PE image."""

import importlib.util
import os
import struct
import tempfile
import unittest
from pathlib import Path

PEREAD = Path(__file__).resolve().parent.parent / "tools" / "usbc_analysis" / "peread.py"
spec = importlib.util.spec_from_file_location("usbc_peread", PEREAD)
peread = importlib.util.module_from_spec(spec)
spec.loader.exec_module(peread)

BASE, RVA, RAW = 0x10000000, 0x1000, 0x200


def build_image(payload, magic=0x10B):
    data = bytearray(RAW + len(payload))
    struct.pack_into("<I", data, 0x3C, 0x40)
    pe = 0x40
    data[pe:pe + 4] = b"PE\0\0"
    struct.pack_into("<H", data, pe + 6, 1)            # one section
    struct.pack_into("<H", data, pe + 20, 0xE0)        # optional header size
    optional = pe + 24
    struct.pack_into("<H", data, optional, magic)
    struct.pack_into("<I", data, optional + 28, BASE)
    section = optional + 0xE0
    # virtual size is larger than the raw data: the tail is uninitialised
    struct.pack_into("<IIII", data, section + 8, len(payload) + 16, RVA, len(payload), RAW)
    data[RAW:] = payload
    return bytes(data)


class PeReadTest(unittest.TestCase):
    def image(self, payload, **kwargs):
        handle, path = tempfile.mkstemp(suffix=".dll")
        os.close(handle)
        self.addCleanup(os.remove, path)
        Path(path).write_bytes(build_image(payload, **kwargs))
        return path

    def test_reads_typed_values_by_virtual_address(self):
        payload = struct.pack("<dfihB", 1.5707963267948966, 0.5, -7, -2, 9) + b"Gearing\0"
        image = peread.Image(self.image(payload))
        address = BASE + RVA
        self.assertEqual(image.read("double", address), 1.5707963267948966)
        self.assertEqual(image.read("float", address + 8), 0.5)
        self.assertEqual(image.read("i32", address + 12), -7)
        self.assertEqual(image.read("i16", address + 16), -2)
        self.assertEqual(image.read("u8", address + 18), 9)
        self.assertEqual(image.read("str", address + 19), "Gearing")

    def test_uninitialised_and_unmapped_addresses_are_errors(self):
        image = peread.Image(self.image(bytes(8)))
        with self.assertRaisesRegex(ValueError, "uninitialised"):
            image.read("u8", BASE + RVA + 8)
        with self.assertRaisesRegex(ValueError, "not in any section"):
            image.read("u8", BASE + 0x900000)

    def test_a_64_bit_image_is_refused(self):
        with self.assertRaisesRegex(ValueError, "32-bit"):
            peread.Image(self.image(bytes(8), magic=0x20B))


if __name__ == "__main__":
    unittest.main()
