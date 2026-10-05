"""Read constants out of a PE file by virtual address. Standard library only.

    python peread.py DLL double 0x10065cc0 [0x...]
    python peread.py DLL float|i32|u32|i16|u16|u8 ADDRESS [...]
    python peread.py DLL str ADDRESS

The file is parsed as bytes; nothing is loaded or run.
"""

import struct
import sys

FORMATS = {"double": "<d", "float": "<f", "i32": "<i", "u32": "<I",
           "i16": "<h", "u16": "<H", "u8": "<B"}


class Image:
    def __init__(self, path):
        with open(path, "rb") as handle:
            self.data = handle.read()
        pe = struct.unpack_from("<I", self.data, 0x3C)[0]
        count = struct.unpack_from("<H", self.data, pe + 6)[0]
        optional_size = struct.unpack_from("<H", self.data, pe + 20)[0]
        optional = pe + 24
        if struct.unpack_from("<H", self.data, optional)[0] != 0x10B:
            raise ValueError("not a 32-bit PE image")
        self.base = struct.unpack_from("<I", self.data, optional + 28)[0]
        self.sections = []
        for index in range(count):
            entry = optional + optional_size + 40 * index
            virtual_size, rva, raw_size, raw_offset = struct.unpack_from("<IIII", self.data,
                                                                         entry + 8)
            self.sections.append((rva, virtual_size, raw_size, raw_offset))

    def offset(self, address):
        rva = address - self.base
        for start, virtual_size, raw_size, raw_offset in self.sections:
            if start <= rva < start + max(virtual_size, raw_size):
                if rva - start >= raw_size:
                    raise ValueError(f"{address:#x} is uninitialised data (not in the file)")
                return raw_offset + rva - start
        raise ValueError(f"{address:#x} is not in any section")

    def read(self, kind, address):
        if kind == "str":
            start = self.offset(address)
            return self.data[start:self.data.index(b"\0", start)].decode("latin-1")
        return struct.unpack_from(FORMATS[kind], self.data, self.offset(address))[0]


def main(argv):
    if len(argv) < 4 or argv[2] not in (*FORMATS, "str"):
        print(__doc__)
        return 2
    image = Image(argv[1])
    for text in argv[3:]:
        address = int(text, 16)
        print(f"{address:#010x} {argv[2]:6s} {image.read(argv[2], address)!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
