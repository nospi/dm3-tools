#!/usr/bin/env python3
"""Byte-archaeology: verify assumed MBDF record layouts across ALL fixture files.

Asserts structural invariants; prints any file that violates them.
Goal: pin down exact offsets before writing the real parser.
"""
import struct
import sys
from collections import Counter
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "factory"


def cstr(b: bytes) -> str:
    return b.split(b"\x00")[0].decode("ascii", "replace")


def u16(b, o):
    return struct.unpack_from("<H", b, o)[0]


def u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def check(path: Path, stats: Counter, problems: list):
    d = path.read_bytes()
    if not d.startswith(b"#YAMAHA MBDF"):
        problems.append((path.name, "bad magic"))
        return

    ftype = cstr(d[12:0x24])
    stats[f"type:{ftype}"] += 1

    # header region 0x0c..0x24 beyond type name
    # 0x24: product
    product = cstr(d[0x24:0x34])
    stats[f"product:{product}"] += 1
    # 0x34..0x38
    stats[f"hdr2:{d[0x34:0x38].hex()}"] += 1

    # field scan
    offs = []
    i = 0
    while True:
        j = d.find(b"#MMS FIELD", i)
        if j < 0:
            break
        offs.append(j)
        i = j + 1
    ends = offs[1:] + [len(d)]

    for j, end in zip(offs, ends):
        # layout guess: "#MMS FIELD\0\0" (12) + name[20] + uint32 + uint32 -> data...
        name = cstr(d[j + 12 : j + 32])
        v1 = u32(d, j + 32)
        v2 = u32(d, j + 36)
        span = end - j
        # try to relate v1/v2 to span
        stats[f"fieldname:{name}"] += 1
        rel = []
        for label, v in (("v1", v1), ("v2", v2)):
            if v == span:
                rel.append(f"{label}==span")
            elif v == span - 40:
                rel.append(f"{label}==span-40")
        key = f"fieldrel:{name}:{'|'.join(rel) if rel else f'none(v1={v1},v2={v2},span={span})'}"
        stats[key] += 1

        # MMSXLIT should follow at j+40
        if d[j + 40 : j + 47] != b"MMSXLIT":
            problems.append((path.name, f"no MMSXLIT at field+40 (field {name} @{j:#x})"))
            continue
        x = j + 40
        xname = cstr(d[x + 8 : x + 40])
        digest = d[x + 40 : x + 72]
        try:
            bytes.fromhex(digest.decode("ascii"))
            stats["digest:hex32"] += 1
        except Exception:
            problems.append((path.name, f"digest not 32-hex at {x + 40:#x}: {digest!r}"))
        xv1 = u32(d, x + 72)
        xv2 = u32(d, x + 76)
        xv3 = u32(d, x + 80)
        stats[f"xlit:{xname}:v=({xv1},{xv2},{xv3})"] += 1

    # PR record shape: verify kind byte values and that name is ascii
    i = 0
    while True:
        j = d.find(b"PR \x00", i)
        if j < 0:
            break
        kind = d[j + 3]
        size = u16(d, j + 4)
        # record where the fourth byte is the kind... actually "PR " + kind at j+3
        i = j + 1
    # count PR with each kind byte (scan differently: records are 32-byte aligned within tables)
    i = 0
    while True:
        j = d.find(b"PR ", i)
        if j < 0:
            break
        kind = d[j + 3]
        size = u16(d, j + 4)
        count = u16(d, j + 6)
        name = d[j + 8 : j + 32]
        if all(32 <= c < 127 or c == 0 for c in name):
            stats[f"prkind:{kind:#04x}"] += 1
            if count == 0:
                stats["pr:count0"] += 1
        else:
            stats["pr:nonascii-name"] += 1
        i = j + 32 if all(32 <= c < 127 or c == 0 for c in name) else j + 1


def main():
    stats = Counter()
    problems = []
    files = sorted(FIXTURES.rglob("*.dm3p")) + sorted(FIXTURES.rglob("*.dm3s"))
    for f in files:
        check(f, stats, problems)
    print(f"checked {len(files)} files")
    for k, v in sorted(stats.items()):
        print(f"  {v:5d}  {k}")
    print(f"\nproblems: {len(problems)}")
    for name, msg in problems[:20]:
        print(f"  {name}: {msg}")


if __name__ == "__main__":
    main()
