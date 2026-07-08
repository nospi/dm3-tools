#!/usr/bin/env python3
"""Generate reusable per-instrument DM3 channel presets (.dm3p).

Each preset in presets/channel-library.yaml becomes a standalone .dm3p channel
library file that the DM3 (or DM3 Editor) can recall onto any input channel.

Method: clone a factory single-channel (scope=CH) preset as a byte-template,
then overwrite just two things —
  * the Mixing:CH strip  (build_strip from apply_presets.py)
  * the PresetInfo metadata (Title / Name / Category / Comment)
— and stamp a fresh object UUID so the desk treats each as its own library slot.
Everything else in the template (Patch/Fader/sends/Processing) is byte-preserved
because the codec encodes partially over the original bytes.

Usage:
    generate_presets.py [--template FILE] [--outdir presets/dm3p]

Descriptor dir: $DM3_DESCRIPTOR_DIR, else repo fixtures/descriptors.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

from dm3tools import codec, descriptors, mbdf  # noqa: E402
from apply_presets import build_strip, load_library, _descriptor_dir  # noqa: E402

# A clean factory single-channel (scope=CH) preset used purely as a byte donor.
DEFAULT_TEMPLATE = (
    REPO / "fixtures/factory/Library/BankA/3DC5C68EE40D423E90C5F0591480C03D.dm3p"
)
UUID_OFF = 0x38  # object UUID lives at header 0x38:0x48


def _set_field(m, funcs, function, scope, values):
    fld = next(
        (f for f in m.fields if f.function == function and (scope is None or f.scope == scope)),
        None,
    )
    if fld is None:
        sys.exit(f"error: template missing field {function} (scope={scope})")
    fld.data = codec.encode_field(funcs[function], fld, values)


def generate(name, preset, template_bytes, funcs, outdir):
    # fresh byte image per preset so UUID stamping / edits don't accumulate
    tmp = outdir / f"{name}.dm3p"
    tmp.write_bytes(template_bytes)
    m = mbdf.parse_file(str(tmp))

    # unique object UUID -> its own library slot on the desk
    raw = bytearray(m.raw)
    new_uuid = os.urandom(16)
    raw[UUID_OFF : UUID_OFF + 16] = new_uuid
    m.raw = bytes(raw)
    m.uuid = new_uuid

    meta = preset.get("meta", {})
    strip = build_strip(preset)
    strip["Label"] = {
        "Name": meta.get("name", name),
        "Category": meta.get("category", ""),
        "Color": meta.get("color", "Blue"),
        "Icon": meta.get("icon", "DynamicMic"),
    }

    _set_field(m, funcs, "Mixing", "CH", strip)
    _set_field(
        m, funcs, "PresetInfo", None,
        {"Info": {
            "Title": meta.get("title", name),
            "Name": meta.get("name", name),
            "Category": meta.get("category", ""),
            "Comment": meta.get("comment", ""),
            "Color": meta.get("color", "Blue"),
            "Icon": meta.get("icon", "DynamicMic"),
        }},
    )
    m.save(str(tmp))
    return tmp, new_uuid


def verify(path, funcs):
    """Decode back and return the strip + metadata for a spot-check."""
    m = mbdf.parse_file(str(path))
    out = {}
    for f in m.fields:
        key = f.function if not f.scope else f"{f.function}:{f.scope}"
        out[key] = codec.decode_field(funcs[f.function], f)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--template", default=str(DEFAULT_TEMPLATE))
    ap.add_argument("--outdir", default=str(REPO / "presets" / "dm3p"))
    args = ap.parse_args(argv)

    funcs = descriptors.load_all(_descriptor_dir())
    template_bytes = Path(args.template).read_bytes()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    lib = load_library()
    for name, preset in lib.items():
        path, uid = generate(name, preset, template_bytes, funcs, outdir)
        d = verify(path, funcs)
        mx = d["Mixing:CH"]
        info = d["PresetInfo"]["Info"]
        hp = mx["PEQ"]["Bank"]["HPF"]
        print(
            f"  {path.name:18s} uuid={uid.hex()[:8]}…  "
            f"title={info['Title']!r} cat={info['Category']!r}  "
            f"EQ.On={mx['PEQ']['On']} HPF={hp['On']}@{hp['Frequency']}Hz "
            f"Gate={mx['Gate']['On']} Comp={mx['Comp']['On']}"
        )
    print(f"\ngenerated {len(lib)} channel presets in {outdir}")


if __name__ == "__main__":
    main()
