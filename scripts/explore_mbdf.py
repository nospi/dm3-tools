#!/usr/bin/env python3
"""Exploratory walker for Yamaha MBDF files (.dm3p/.dm3s/.dm3f).

Prints the tag structure of a file to help pin down the container layout.
Observed layout (from hexdumps of factory files):

  0x00  "#YAMAHA MBDF" magic, then padded type name ("Preset", ...)
  ...   "DM3" product name
  ...   16-byte binary UUID
  ...   "#MMS FIELD" sections, each containing:
          "MMSXLIT" schema block: function name + 32-hex-char digest + sizes
          "COL0" collection records: name + sizes/counts
          "PR" property records: flags/type/size/count + name
        followed by raw data block(s)
"""
import struct
import sys
from pathlib import Path


def hexs(b: bytes) -> str:
    return b.hex()


def cstr(b: bytes) -> str:
    return b.split(b"\x00")[0].decode("ascii", "replace")


def walk(path: Path, verbose: bool = False) -> None:
    data = path.read_bytes()
    print(f"=== {path.name}  ({len(data)} bytes)")

    # header
    print(f"  magic      : {data[0:12]!r}")
    print(f"  type       : {cstr(data[12:0x24])!r}")
    print(f"  hdr bytes  : {hexs(data[0x18:0x24])}")
    print(f"  product    : {cstr(data[0x24:0x34])!r}")
    print(f"  hdr2       : {hexs(data[0x34:0x38])}")
    print(f"  uuid?      : {hexs(data[0x38:0x48])}")

    # find all tag markers
    for tag in (b"#MMS FIELD", b"MMSXLIT", b"COL0", b"PR ", b"#YAMAHA"):
        offs = []
        i = 0
        while True:
            j = data.find(tag, i)
            if j < 0:
                break
            offs.append(j)
            i = j + 1
        print(f"  {tag!r:>14}: {len(offs):3d} at {offs[:8]}{'...' if len(offs) > 8 else ''}")

    # dump MMS FIELD headers
    i = 0
    while True:
        j = data.find(b"#MMS FIELD", i)
        if j < 0:
            break
        name = cstr(data[j + 12 : j + 12 + 16])
        after = data[j + 10 : j + 48]
        print(f"  FIELD @{j:#06x}: name={name!r} raw={hexs(after)}")
        i = j + 1

    # dump MMSXLIT headers
    i = 0
    while True:
        j = data.find(b"MMSXLIT", i)
        if j < 0:
            break
        name = cstr(data[j + 8 : j + 8 + 32])
        rest = data[j + 40 : j + 40 + 44]
        print(f"  XLIT  @{j:#06x}: name={name!r}")
        print(f"      digest? {rest[:32]!r}")
        print(f"      sizes   {hexs(rest[32:44])} -> {struct.unpack('<3I', rest[32:44])}")
        i = j + 1

    if verbose:
        # dump first few COL0 and PR records
        shown = 0
        i = 0
        while shown < 12:
            j = data.find(b"COL0", i)
            if j < 0:
                break
            name = cstr(data[j + 4 : j + 4 + 32])
            nums = struct.unpack("<4I", data[j + 36 : j + 52])
            print(f"  COL0  @{j:#06x}: {name!r:24} nums={nums}")
            i = j + 1
            shown += 1
        shown = 0
        i = 0
        while shown < 20:
            j = data.find(b"PR ", i)
            if j < 0:
                break
            flags = data[j + 3 : j + 12]
            name = cstr(data[j + 12 : j + 12 + 32])
            print(f"  PR    @{j:#06x}: {name!r:28} flags={hexs(flags)}")
            i = j + 1
            shown += 1


if __name__ == "__main__":
    verbose = "-v" in sys.argv
    paths = [Path(a) for a in sys.argv[1:] if not a.startswith("-")]
    for p in paths:
        walk(p, verbose)
        print()
