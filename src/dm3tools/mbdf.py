"""Low-level reader for Yamaha MBDF container files (.dm3p / .dm3s / .dm3f).

Format (reverse-engineered from DM3 Editor factory files, firmware V3.0):

File header
    +0x00  "#YAMAHA MBDF"          magic (12 bytes)
    +0x0c  file type               NUL-padded ASCII ("Preset", "Scene", ...)
    +0x18  12 bytes                header words (observed: 00...24 00...)
    +0x24  product                 NUL-padded ASCII ("DM3")
    +0x34  4 bytes                 version-ish words (observed 52 02 00 07)
    +0x38  16 bytes                binary UUID of this object
    +0x48  first "#MMS FIELD"

MMS field section
    +0x00  "#MMS FIELD\0\0"        tag (12 bytes)
    +0x0c  field name              NUL-padded ASCII, 16 bytes
    +0x1c  8 or 16 bytes           size words (big-endian!) and optional
                                   8-byte scope tag (e.g. "CH") before MMSXLIT
    then   "MMSXLIT" block
    then   consecutive COL0 / PR records (the schema table)
    then   raw data block

MMSXLIT block
    +0x00  "MMSXLIT\0"             tag (8 bytes)
    +0x08  function name           NUL-padded ASCII, 32 bytes
    +0x28  4 bytes                 zeros
    +0x2c  digest                  32 ASCII hex chars (schema version pin)
    +0x4c  4 bytes                 zeros
    +0x50  uint32 LE               records-table size == offset from +0x58
                                   (records start) to the data block
    +0x54  uint32 LE               data block size
    +0x58  records begin; data block follows immediately after records

COL0 record (48 bytes): collection (interior node of the parameter tree)
    +0x00  "COL0"
    +0x04  name                    NUL-padded ASCII, 28 bytes
    +0x20  uint32 LE               offset (within parent scope, in "cells")
    +0x24  uint32 LE               datasize of one element
    +0x28  uint32 LE               arraysize
    +0x2c  uint32 LE               runtime pointer (garbage on disk)

PR record (32 bytes): parameter (leaf)
    +0x00  "PR "
    +0x03  uint8                   kind (0=string, 1=uint-ish, 2=int-ish)
    +0x04  uint16 LE               element size in bytes
    +0x06  uint16 LE               arraysize
    +0x08  name                    NUL-padded ASCII, 24 bytes

All observations validated by assertion across the 278 factory files.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

MAGIC = b"#YAMAHA MBDF"
FIELD_TAG = b"#MMS FIELD\x00\x00"
XLIT_TAG = b"MMSXLIT\x00"


def _cstr(b: bytes) -> str:
    return b.split(b"\x00")[0].decode("ascii", "replace")


@dataclass
class Col:
    name: str
    offset: int
    datasize: int
    arraysize: int
    raw: bytes = b""


@dataclass
class Pr:
    name: str
    kind: int
    size: int
    arraysize: int
    raw: bytes = b""


@dataclass
class MmsField:
    name: str
    scope: str  # e.g. "CH" for per-channel preset fields, "" otherwise
    function: str  # MMSXLIT function name, matches mms_<function>.xml
    digest: str  # 32-hex schema digest
    data_offset: int  # from XLIT start
    data_size: int
    records: list  # Col | Pr, in declaration order
    data: bytes
    span: tuple[int, int]  # (start, end) offsets in file
    data_start: int = 0  # absolute offset of data block in file
    header_raw: bytes = b""

    @property
    def cols(self):
        return [r for r in self.records if isinstance(r, Col)]

    @property
    def prs(self):
        return [r for r in self.records if isinstance(r, Pr)]


@dataclass
class MbdfFile:
    path: Path | None
    file_type: str
    product: str
    uuid: bytes
    header_raw: bytes
    raw: bytes = b""
    fields: list = field(default_factory=list)

    def field_by_function(self, function: str) -> MmsField | None:
        for f in self.fields:
            if f.function == function:
                return f
        return None

    def to_bytes(self) -> bytes:
        """Serialise, patching each field's (possibly modified) data block
        back into the original byte image. Everything outside data blocks is
        preserved byte-for-byte."""
        buf = bytearray(self.raw)
        for f in self.fields:
            if len(f.data) != f.data_size:
                raise MbdfError(
                    f"field {f.name}: data is {len(f.data)} bytes, "
                    f"expected {f.data_size}"
                )
            buf[f.data_start : f.data_start + f.data_size] = f.data
        return bytes(buf)

    def save(self, path: str | Path) -> None:
        Path(path).write_bytes(self.to_bytes())


class MbdfError(ValueError):
    pass


def _parse_records(buf: bytes, pos: int, end: int):
    """Parse consecutive COL0/PR records starting at pos; return (records, pos)."""
    records = []
    while pos < end:
        tag = buf[pos : pos + 4]
        if tag == b"COL0":
            raw = buf[pos : pos + 48]
            name = _cstr(raw[4:32])
            off, size, count, _ptr = struct.unpack_from("<4I", raw, 32)
            records.append(Col(name, off, size, count, raw))
            pos += 48
        elif tag[:3] == b"PR ":
            raw = buf[pos : pos + 32]
            kind = raw[3]
            size, count = struct.unpack_from("<HH", raw, 4)
            name = _cstr(raw[8:32])
            records.append(Pr(name, kind, size, count, raw))
            pos += 32
        else:
            break
    return records, pos


def _parse_field(buf: bytes, start: int, end: int) -> MmsField:
    name = _cstr(buf[start + 0x0C : start + 0x1C])
    # MMSXLIT sits at +36 normally, +44 when an 8-byte scope tag is present
    xoff = None
    scope = ""
    for cand in (36, 44):
        if buf[start + cand : start + cand + 8] == XLIT_TAG:
            xoff = start + cand
            break
    if xoff is None:
        raise MbdfError(f"no MMSXLIT near field {name!r} at {start:#x}")
    if xoff == start + 44:
        scope = _cstr(buf[start + 36 : start + 44])

    function = _cstr(buf[xoff + 8 : xoff + 40])
    digest = buf[xoff + 0x2C : xoff + 0x4C].decode("ascii", "replace")
    data_offset, data_size = struct.unpack_from("<II", buf, xoff + 0x50)

    records, rec_end = _parse_records(buf, xoff + 0x58, end)

    data_start = xoff + 0x58 + data_offset
    data = buf[data_start : data_start + data_size]
    return MmsField(
        name=name,
        scope=scope,
        function=function,
        digest=digest,
        data_offset=data_offset,
        data_size=data_size,
        records=records,
        data=data,
        span=(start, end),
        data_start=data_start,
        header_raw=buf[start : xoff + 0x58],
    )


def parse(data: bytes, path: Path | None = None) -> MbdfFile:
    if not data.startswith(MAGIC):
        raise MbdfError(f"not an MBDF file (magic {data[:12]!r})")
    file_type = _cstr(data[0x0C:0x24])
    product = _cstr(data[0x24:0x34])
    uuid = data[0x38:0x48]
    header_raw = data[0:0x48]

    # locate field sections
    offs = []
    i = 0
    while (j := data.find(FIELD_TAG, i)) >= 0:
        offs.append(j)
        i = j + 1
    if not offs:
        raise MbdfError("no #MMS FIELD sections found")
    ends = offs[1:] + [len(data)]

    mbdf = MbdfFile(path, file_type, product, uuid, header_raw, raw=data)
    for start, end in zip(offs, ends):
        mbdf.fields.append(_parse_field(data, start, end))
    return mbdf


def parse_file(path: str | Path) -> MbdfFile:
    p = Path(path)
    return parse(p.read_bytes(), p)
