"""Yamaha .DM3F show/project files.

A .dm3f is an MBDF file (type "ProjectFile") holding a ProjectInfo field,
followed by a series of embedded files and an end marker:

#FILE entry
    +0x00  "#FILE" + 7 NULs        tag (12 bytes)
    +0x0c  slot name               NUL-padded ASCII, 12 bytes
                                   ("Current", "Scene:A", "SceneList", ...)
    +0x18  uint32 BE               filename length + 8
    +0x1c  uint32 BE               compressed payload size
    +0x20  uint32 BE               0
    +0x24  uint16 BE               filename length
    +0x26  uint16 BE               0
    +0x28  uint32 BE               8
    +0x2c  filename                ASCII, then NUL padding to 4-byte align
    then   zlib stream (payload decompresses to another MBDF file)
    then   NUL padding to 4-byte alignment, next #FILE

Footer: "#END" + 32 NULs (4-byte aligned).

Slots observed: "Current" holds CurrentBackupFile.bup (the console's full
current memory, MBDF type "Backup") plus a ".old" journal copy; "Scene:X"
holds one <UUID>.dm3s per stored scene in bank X; "SceneList" holds the
scene-list index (+ ".old").
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from .mbdf import MbdfError, parse as parse_mbdf

FILE_TAG = b"#FILE" + b"\x00" * 7
END_TAG = b"#END"


def _align4(n: int) -> int:
    return (n + 3) & ~3


@dataclass
class Dm3fEntry:
    slot: str
    filename: str
    payload: bytes  # decompressed

    def parse(self):
        """Parse the payload as an MBDF file."""
        return parse_mbdf(self.payload)


@dataclass
class Dm3f:
    path: Path | None
    prefix: bytes  # MBDF header + Project field, up to first #FILE
    entries: list = field(default_factory=list)

    @property
    def project(self):
        return parse_mbdf(self.prefix + b"")  # prefix is a complete MBDF image

    def entry(self, name: str) -> Dm3fEntry | None:
        """Find an entry by filename or slot name (first match)."""
        for e in self.entries:
            if e.filename == name or e.slot == name:
                return e
        return None

    def to_bytes(self) -> bytes:
        out = bytearray(self.prefix)
        for e in self.entries:
            comp = zlib.compress(e.payload, 1)
            fn = e.filename.encode("ascii")
            hdr = FILE_TAG
            hdr += e.slot.encode("ascii").ljust(12, b"\x00")
            hdr += struct.pack(">IIIHHI", len(fn) + 8, len(comp), 0, len(fn), 0, 8)
            body = hdr + fn
            body += b"\x00" * (_align4(len(body)) - len(body))
            body += comp
            body += b"\x00" * (_align4(len(comp)) - len(comp))
            out += body
        out += b"\x00" * (_align4(len(out)) - len(out))
        out += END_TAG + b"\x00" * 32
        return bytes(out)

    def save(self, path: str | Path) -> None:
        Path(path).write_bytes(self.to_bytes())


def parse(data: bytes, path: Path | None = None) -> Dm3f:
    if not data.startswith(b"#YAMAHA MBDF"):
        raise MbdfError("not an MBDF file")
    first = data.find(FILE_TAG)
    if first < 0:
        raise MbdfError("no #FILE entries: not a .dm3f project file?")
    dm3f = Dm3f(path, prefix=data[:first])

    pos = first
    while True:
        if data[pos : pos + 4] == END_TAG or pos >= len(data):
            break
        if data[pos : pos + 12] != FILE_TAG:
            # skip alignment padding
            if data[pos] == 0:
                pos += 1
                continue
            raise MbdfError(f"unexpected bytes at {pos:#x}: {data[pos:pos+8]!r}")
        slot = data[pos + 12 : pos + 24].split(b"\x00")[0].decode("ascii")
        len_a, csize, z1, fnlen, z2, c8 = struct.unpack_from(">IIIHHI", data, pos + 24)
        if len_a != fnlen + 8 or z1 or z2 or c8 != 8:
            raise MbdfError(f"bad #FILE header at {pos:#x}")
        filename = data[pos + 44 : pos + 44 + fnlen].decode("ascii")
        pstart = _align4(pos + 44 + fnlen)
        payload = zlib.decompress(data[pstart : pstart + csize])
        dm3f.entries.append(Dm3fEntry(slot, filename, payload))
        pos = _align4(pstart + csize)
    return dm3f


def parse_file(path: str | Path) -> Dm3f:
    p = Path(path)
    return parse(p.read_bytes(), p)
