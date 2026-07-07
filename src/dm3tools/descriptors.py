"""Parser for Yamaha DM3 descriptor XMLs (mms_*.xml).

These ship with DM3 Editor (Descriptor/ directory) and define the complete
parameter tree per "function" (Mixing, SceneInfo, Setup, ...): names, C types,
ranges, defaults and struct layout sizes. The binary data blocks in MBDF files
are packed structs laid out exactly as these trees declare.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

_INT_TYPES = {
    "uint8_t": (1, False),
    "int8_t": (1, True),
    "uint16_t": (2, False),
    "int16_t": (2, True),
    "uint32_t": (4, False),
    "int32_t": (4, True),
    "uint64_t": (8, False),
    "int64_t": (8, True),
}


def _parse_num(s: str | None):
    if s is None:
        return None
    s = s.strip().rstrip("UL").rstrip("u")
    try:
        return int(s, 0)
    except ValueError:
        try:
            return float(s)
        except ValueError:
            return s


@dataclass
class Param:
    name: str
    type: str  # "string" or C int type
    arraysize: int
    minimum: object = None
    maximum: object = None
    default: object = None
    step: object = None
    scaling: object = None  # raw = display * scaling; display = raw / scaling
    unit: str | None = None
    kind: int | None = None

    @property
    def is_string(self) -> bool:
        return self.type == "string"

    @property
    def elem_size(self) -> int:
        if self.is_string:
            # strings declare max length; stored size observed = maximum bytes
            return int(self.maximum)
        return _INT_TYPES[self.type][0]

    @property
    def signed(self) -> bool:
        return not self.is_string and _INT_TYPES[self.type][1]


@dataclass
class Collection:
    name: str
    datasize: int
    arraysize: int
    children: list = field(default_factory=list)  # Collection | Param


@dataclass
class Function:
    name: str
    collections: int
    parameters: int
    datasize: int
    stringsize: int
    children: list = field(default_factory=list)


def _parse_children(elem) -> list:
    out = []
    for child in elem:
        if child.tag == "collection":
            col = Collection(
                name=child.get("name"),
                datasize=int(child.get("datasize", 0)),
                arraysize=int(child.get("arraysize", 1)),
                children=_parse_children(child),
            )
            out.append(col)
        elif child.tag == "parameter":
            kind = child.find("kind")
            out.append(
                Param(
                    name=child.get("name"),
                    type=child.get("type"),
                    arraysize=int(child.get("arraysize", 1)),
                    minimum=_parse_num(child.findtext("minimum")),
                    maximum=_parse_num(child.findtext("maximum")),
                    default=child.findtext("default"),
                    step=_parse_num(child.findtext("step")),
                    scaling=_parse_num(child.findtext("scaling")),
                    unit=child.findtext("unit"),
                    kind=int(kind.text) if kind is not None else None,
                )
            )
    return out


def parse_descriptor(path: str | Path) -> Function:
    root = ET.parse(path).getroot()
    cols = root.find("collections")
    return Function(
        name=root.get("name"),
        collections=int(root.get("collections", 0)),
        parameters=int(root.get("parameters", 0)),
        datasize=int(root.get("datasize", 0)),
        stringsize=int(root.get("stringsize", 0)),
        children=_parse_children(cols if cols is not None else root),
    )


def load_all(descriptor_dir: str | Path) -> dict[str, Function]:
    funcs = {}
    for p in sorted(Path(descriptor_dir).glob("mms_*.xml")):
        f = parse_descriptor(p)
        funcs[f.name] = f
    return funcs
