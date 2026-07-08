#!/usr/bin/env python3
"""Apply channel-strip presets into an existing DM3 scene/show file.

The presets in presets/channel-library.yaml are authored in REAL units
(Hz / dB / ms / ratio); the codec applies the descriptor's fixed-point
<scaling>, so values pass straight through. Only the fields each preset
defines are written — everything else in the file is byte-preserved
(encode_field starts from the existing data block).

Usage:
    # one preset onto one channel (1-based channel numbers, as on the desk)
    apply_presets.py FILE --channel 14 --preset vox [-o OUT] [--entry E]

    # batch: a channel->preset map applied in a single pass
    apply_presets.py FILE --map "1=kick,2=snare,9=bass,14=vox" [-o OUT]

Presets are STARTING POINTS — the operator dials Comp/Gate threshold and
makeup gain live (see the `# DIAL` markers in the library).

Descriptor dir: $DM3_DESCRIPTOR_DIR, else repo fixtures/descriptors.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from dm3tools import codec, descriptors, dm3f as dm3f_mod, mbdf  # noqa: E402

LIBRARY = REPO / "presets" / "channel-library.yaml"
DEFAULT_ENTRY = "CurrentBackupFile.bup"
N_INPUT_CHANNELS = 16


def _descriptor_dir() -> Path:
    env = os.environ.get("DM3_DESCRIPTOR_DIR")
    if env:
        return Path(env)
    cand = REPO / "fixtures" / "descriptors"
    if cand.is_dir():
        return cand
    sys.exit(
        "error: descriptors not found. Set DM3_DESCRIPTOR_DIR to the DM3 "
        "Editor Descriptor/ directory."
    )


def _is_dm3f(path: str) -> bool:
    with open(path, "rb") as fh:
        head = fh.read(0x24)
    return head[0x0C:0x17] == b"ProjectFile"


def load_library() -> dict:
    with open(LIBRARY) as fh:
        return yaml.safe_load(fh)


def _band(b: dict) -> dict:
    return {
        "Type": b["type"],
        "Frequency": b["freq_hz"],
        "Gain": b["gain_db"],
        "Q": b["q"],
    }


def _gate(g: dict) -> dict:
    return {
        "Threshold": g["threshold_db"],
        "Attack": g["attack_ms"],
        "Range": g["range_db"],
        "Hold": g["hold_ms"],
        "Decay": g["decay_ms"],
    }


def _comp(c: dict) -> dict:
    return {
        "Threshold": c["threshold_db"],
        "Attack": c["attack_ms"],
        "Release": c["release_ms"],
        "Ratio": c["ratio"],
        "Knee": c.get("knee", "SOFT-2"),
        "Gain": c["gain_db"],
    }


def build_strip(preset: dict) -> dict:
    """Translate one YAML preset into a sparse InputChannel[n] value tree.

    "Disengaged but dialled-in": every block's VALUES are always written so a
    parked comp/gate is ready to flip on; the `on:` flag only sets the engage
    bit. HPF/EQ default on (the corrective starting sound), Gate/Comp off.
    Only strip fields are populated; the codec preserves everything else
    (patch, label, fader, sends, ...) because encode starts from real bytes.
    """
    h = preset["hpf"]
    eq = preset["eq"]
    strip = {
        "PEQ": {
            "On": 1 if eq.get("on", True) else 0,
            "Bank": {
                "OneKnob": {"On": 0},  # force manual bands, never 1-knob EQ
                "HPF": {
                    "On": 1 if h.get("on", True) else 0,
                    "Frequency": h["freq_hz"],
                    "Slope": 12,
                },
                "BandL": _band(eq["low"]),
                "Band": [_band(eq["mid1"]), _band(eq["mid2"])],
                "BandH": _band(eq["high"]),
            },
        },
        "Gate": {
            "On": 1 if preset["gate"].get("on") else 0,
            "Bank": {"Gate": _gate(preset["gate"])},
        },
        "Comp": {
            "On": 1 if preset["comp"].get("on") else 0,
            "Bank": {"Compressor": _comp(preset["comp"])},
        },
    }
    # polarity flip (e.g. under-snare mic): only touch Input.Phase when the
    # preset declares it, so channels that don't care are left byte-untouched.
    if "phase" in preset:
        strip["Input"] = {"Phase": 1 if preset["phase"] else 0}
    return strip


def parse_map(spec: str) -> dict[int, str]:
    """'1=kick,14=vox' -> {0: 'kick', 13: 'vox'} (1-based in, 0-based out)."""
    out: dict[int, str] = {}
    for pair in spec.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            sys.exit(f"error: bad map entry {pair!r} (want CH=preset)")
        ch_s, name = (x.strip() for x in pair.split("=", 1))
        ch = int(ch_s)
        if not 1 <= ch <= N_INPUT_CHANNELS:
            sys.exit(f"error: channel {ch} out of range 1..{N_INPUT_CHANNELS}")
        out[ch - 1] = name
    return out


def apply(path, mapping, out, entry):
    lib = load_library()
    for name in mapping.values():
        if name not in lib:
            sys.exit(
                f"error: unknown preset {name!r}. "
                f"Available: {', '.join(sorted(lib))}"
            )

    funcs = descriptors.load_all(_descriptor_dir())
    container = None
    if _is_dm3f(path):
        container = dm3f_mod.parse_file(path)
        entry = entry or DEFAULT_ENTRY
        e = container.entry(entry)
        if e is None:
            sys.exit(f"error: no entry {entry!r} in {path}")
        m = e.parse()
    else:
        m = mbdf.parse_file(path)

    fld = next((f for f in m.fields if f.function == "Mixing"), None)
    if fld is None:
        sys.exit("error: no Mixing field in file")

    ic = [{} for _ in range(N_INPUT_CHANNELS)]
    for idx, name in mapping.items():
        ic[idx] = build_strip(lib[name])
    fld.data = codec.encode_field(funcs["Mixing"], fld, {"InputChannel": ic})

    out = out or path
    if container is not None:
        container.entry(entry).payload = m.to_bytes()
        container.save(out)
    else:
        m.save(out)

    applied = ", ".join(f"ch{idx + 1}={name}" for idx, name in sorted(mapping.items()))
    print(f"wrote {out}  ({applied})")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file")
    ap.add_argument("--channel", "-c", type=int, help="1-based channel number")
    ap.add_argument("--preset", "-p", help="preset name (with --channel)")
    ap.add_argument("--map", "-m", help='batch, e.g. "1=kick,14=vox"')
    ap.add_argument("--output", "-o", help="write here instead of in place")
    ap.add_argument("--entry", "-e", help=".dm3f entry (default: current memory)")
    ap.add_argument("--list", action="store_true", help="list presets and exit")
    args = ap.parse_args(argv)

    if args.list:
        for name in sorted(load_library()):
            print(name)
        return

    if args.map:
        mapping = parse_map(args.map)
    elif args.channel and args.preset:
        if not 1 <= args.channel <= N_INPUT_CHANNELS:
            sys.exit(f"error: channel out of range 1..{N_INPUT_CHANNELS}")
        mapping = {args.channel - 1: args.preset}
    else:
        ap.error("need --map, or both --channel and --preset (or --list)")

    apply(args.file, mapping, args.output, args.entry)


if __name__ == "__main__":
    main()
