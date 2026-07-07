"""Descriptor-driven decode/encode of MBDF data blocks.

Layout rule (validated against factory files): a function's data block is a
packed little-endian struct laid out by depth-first walk of the descriptor
tree. Each collection occupies datasize * arraysize bytes; its children pack
sequentially inside each element; string parameters occupy their declared
maximum length inline (NUL-padded).

Scoped fields (preset subtrees like scope="CH" -> InputChannel) are resolved
by matching the field's first embedded COL0 record name against a collection
in the descriptor tree.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .descriptors import Collection, Function, Param
from .mbdf import Col, MmsField


class CodecError(ValueError):
    pass


# ---------------------------------------------------------------- layout ----


@dataclass
class Leaf:
    path: str
    param: Param
    offset: int  # byte offset of element 0 within the data block
    index: int  # element index when parent collections are arrays


def _sum_children(children) -> int:
    total = 0
    for c in children:
        if isinstance(c, Collection):
            total += c.datasize * c.arraysize
        else:
            total += c.elem_size * c.arraysize
    return total


def find_collection(node, name: str):
    """Depth-first search for a collection by name."""
    children = node.children if hasattr(node, "children") else []
    for c in children:
        if isinstance(c, Collection):
            if c.name == name:
                return c
            found = find_collection(c, name)
            if found is not None:
                return found
    return None


def resolve_tree(function: Function, mms_field: MmsField):
    """Return (root_children, root_arraysize, root_name) for a field.

    Full-function fields use the function's children. Scoped fields (presets)
    use the subtree named by the field's first COL0 record; a single element
    of that collection is stored (arraysize forced to 1).
    """
    cols = mms_field.cols
    if not mms_field.scope:
        return function.children, function.name
    if not cols:
        raise CodecError(f"scoped field {mms_field.name} has no COL0 records")
    root = find_collection(function, cols[0].name)
    if root is None:
        raise CodecError(
            f"collection {cols[0].name!r} (scope {mms_field.scope}) "
            f"not found in descriptor {function.name}"
        )
    return root.children, f"{function.name}.{root.name}"


# ---------------------------------------------------------------- decode ----


def _decode_param(p: Param, buf: bytes, off: int):
    if p.is_string:
        size = p.elem_size
        vals = []
        for i in range(p.arraysize):
            raw = buf[off + i * size : off + (i + 1) * size]
            vals.append(raw.split(b"\x00")[0].decode("utf-8", "replace"))
        return vals[0] if p.arraysize == 1 else vals
    size = p.elem_size
    fmt = {1: "b", 2: "h", 4: "i", 8: "q"}[size]
    if not p.signed:
        fmt = fmt.upper()
    vals = list(struct.unpack_from(f"<{p.arraysize}{fmt}", buf, off))
    # fixed-point: descriptor declares a scaling divisor (raw = display * scaling)
    sc = getattr(p, "scaling", None)
    if sc and sc != 1:
        vals = [v / sc for v in vals]
    return vals[0] if p.arraysize == 1 else vals


def decode_children(children, buf: bytes, base: int) -> dict:
    out = {}
    off = base
    for c in children:
        if isinstance(c, Collection):
            if c.arraysize == 1:
                out[c.name] = decode_children(c.children, buf, off)
            else:
                out[c.name] = [
                    decode_children(c.children, buf, off + i * c.datasize)
                    for i in range(c.arraysize)
                ]
            off += c.datasize * c.arraysize
        else:
            out[c.name] = _decode_param(c, buf, off)
            off += c.elem_size * c.arraysize
    return out


def decode_field(function: Function, mms_field: MmsField) -> dict:
    children, label = resolve_tree(function, mms_field)
    expected = _sum_children(children)
    if expected != mms_field.data_size:
        raise CodecError(
            f"{label}: descriptor layout is {expected} bytes but data block "
            f"is {mms_field.data_size} bytes"
        )
    return decode_children(children, mms_field.data, 0)


# ---------------------------------------------------------------- encode ----


def _encode_param(p: Param, value, buf: bytearray, off: int):
    if p.is_string:
        size = p.elem_size
        vals = [value] if p.arraysize == 1 else list(value)
        for i, v in enumerate(vals):
            raw = str(v).encode("utf-8")[:size]
            b0 = off + i * size
            buf[b0 : b0 + len(raw)] = raw
            # NUL-terminate if there's room, but PRESERVE the original bytes
            # after the terminator. Factory files leave uninitialised garbage
            # past the NUL; zero-padding it breaks byte-exact round-trips.
            # buf starts as the original data block, so the tail is intact.
            # (Full-width strings fill `size` exactly and get no terminator.)
            if len(raw) < size:
                buf[b0 + len(raw)] = 0
        return
    size = p.elem_size
    fmt = {1: "b", 2: "h", 4: "i", 8: "q"}[size]
    if not p.signed:
        fmt = fmt.upper()
    vals = [value] if p.arraysize == 1 else list(value)
    # invert the fixed-point scaling; round onto the exact integer grid
    sc = getattr(p, "scaling", None)
    if sc and sc != 1:
        raw = [round(v * sc) for v in vals]
    else:
        raw = [int(v) for v in vals]
    struct.pack_into(f"<{p.arraysize}{fmt}", buf, off, *raw)


def encode_children(children, values: dict, buf: bytearray, base: int):
    off = base
    for c in children:
        if isinstance(c, Collection):
            if c.name in values:
                if c.arraysize == 1:
                    encode_children(c.children, values[c.name], buf, off)
                else:
                    for i, elem in enumerate(values[c.name]):
                        encode_children(
                            c.children, elem, buf, off + i * c.datasize
                        )
            off += c.datasize * c.arraysize
        else:
            if c.name in values:
                _encode_param(c, values[c.name], buf, off)
            off += c.elem_size * c.arraysize


def encode_field(function: Function, mms_field: MmsField, values: dict) -> bytes:
    """Re-encode a field's data block with `values` (full or partial tree)."""
    children, _ = resolve_tree(function, mms_field)
    buf = bytearray(mms_field.data)  # start from existing bytes: partial update
    encode_children(children, values, buf, 0)
    return bytes(buf)
