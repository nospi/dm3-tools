"""dm3 — CLI for reading and writing Yamaha DM3 console files.

Supported: .dm3p (preset), .dm3s (scene). (.dm3f show files: in progress.)

Commands:
    dm3 info FILE                        header + field summary
    dm3 dump FILE [--function F]         decode to JSON
    dm3 get FILE PATH                    read one value
    dm3 set FILE PATH=VALUE... [-o OUT]  patch values, write file
    dm3 diff A B [--function F]          compare two files' decoded values
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from . import codec, descriptors, dm3f as dm3f_mod, mbdf

DESCRIPTOR_ENV = "DM3_DESCRIPTOR_DIR"
DEFAULT_ENTRY = "CurrentBackupFile.bup"


def _is_dm3f(path: str) -> bool:
    with open(path, "rb") as fh:
        head = fh.read(0x24)
    return head[0x0C:0x17] == b"ProjectFile"


def _descriptor_dir(cli_arg: str | None) -> Path:
    if cli_arg:
        return Path(cli_arg)
    if os.environ.get(DESCRIPTOR_ENV):
        return Path(os.environ[DESCRIPTOR_ENV])
    # repo-local fixtures fallback
    here = Path(__file__).resolve()
    for parent in here.parents:
        cand = parent / "fixtures" / "descriptors"
        if cand.is_dir():
            return cand
    sys.exit(
        "error: descriptor XMLs not found. Pass --descriptors or set "
        f"{DESCRIPTOR_ENV} to the DM3 Editor Descriptor/ directory."
    )


def _load(path: str, ddir: Path, entry: str | None = None):
    """Load an MBDF file. For .dm3f project files, loads the embedded entry
    (default: the console's current memory). Returns (mbdf, funcs, container)
    where container is the Dm3f (or None for plain files)."""
    funcs = descriptors.load_all(ddir)
    if _is_dm3f(path):
        container = dm3f_mod.parse_file(path)
        name = entry or DEFAULT_ENTRY
        e = container.entry(name)
        if e is None:
            names = ", ".join(x.filename for x in container.entries)
            sys.exit(f"error: no entry {name!r} in {path} (have: {names})")
        return e.parse(), funcs, container
    return mbdf.parse_file(path), funcs, None


def _decode_all(m, funcs) -> dict:
    out = {}
    for f in m.fields:
        fn = funcs.get(f.function)
        if fn is None:
            out[f.function] = f"<no descriptor {f.function}>"
            continue
        key = f.function if not f.scope else f"{f.function}:{f.scope}"
        try:
            out[key] = codec.decode_field(fn, f)
        except codec.CodecError as e:
            out[key] = f"<decode error: {e}>"
    return out


# ------------------------------------------------------------- path expr ----

_TOKEN = re.compile(r"([A-Za-z0-9_]+)(?:\[(\d+)\])?")


def _parse_path(path: str):
    """'Mixing.InputChannel[0].Label.Name' -> [('Mixing',None),('InputChannel',0),...]"""
    parts = []
    for seg in path.split("."):
        m = _TOKEN.fullmatch(seg)
        if not m:
            sys.exit(f"error: bad path segment {seg!r}")
        parts.append((m.group(1), int(m.group(2)) if m.group(2) else None))
    return parts


def _get_path(tree, parts):
    cur = tree
    for name, idx in parts:
        try:
            cur = cur[name]
            if idx is not None:
                cur = cur[idx]
        except (KeyError, IndexError, TypeError):
            sys.exit(f"error: path not found at {name!r}")
    return cur


def _sparse_from_path(parts, value):
    """Build the nested sparse dict codec.encode_children expects.

    Array collections are represented as {index: subtree} via a list padded
    with empty dicts (encode walks lists positionally)."""
    node = value
    for name, idx in reversed(parts):
        if idx is not None:
            lst = [{} for _ in range(idx + 1)]
            lst[idx] = node
            node = {name: lst}
        else:
            node = {name: node}
    return node


def _merge(a: dict, b: dict):
    for k, v in b.items():
        if k in a and isinstance(a[k], dict) and isinstance(v, dict):
            _merge(a[k], v)
        elif k in a and isinstance(a[k], list) and isinstance(v, list):
            if len(v) > len(a[k]):
                a[k].extend({} for _ in range(len(v) - len(a[k])))
            for i, elem in enumerate(v):
                if elem:
                    if isinstance(a[k][i], dict) and isinstance(elem, dict):
                        _merge(a[k][i], elem)
                    else:
                        a[k][i] = elem
        else:
            a[k] = v
    return a


def _coerce(s: str):
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    return s


# -------------------------------------------------------------- commands ----


def cmd_info(args):
    ddir = _descriptor_dir(args.descriptors)
    if _is_dm3f(args.file):
        container = dm3f_mod.parse_file(args.file)
        funcs = descriptors.load_all(ddir)
        pi = container.project
        info = codec.decode_field(
            funcs["ProjectInfo"], pi.field_by_function("ProjectInfo")
        )["Info"]
        ts = info["TimeStamp"]
        print(f"file      : {args.file}")
        print(f"type      : ProjectFile (.dm3f show)")
        print(
            f"saved     : {ts['Year']:04d}-{ts['Month']:02d}-{ts['Day']:02d} "
            f"{ts['Hour']:02d}:{ts['Minute']:02d}:{ts['Second']:02d} UTC"
        )
        print("entries:")
        for e in container.entries:
            m = e.parse()
            fns = ",".join(x.function for x in m.fields)
            print(f"  [{e.slot:9s}] {e.filename:42s} {len(e.payload):7d}B  {fns}")
        return
    m, funcs, _ = _load(args.file, ddir)
    print(f"file      : {args.file}")
    print(f"type      : {m.file_type}")
    print(f"product   : {m.product}")
    print(f"uuid      : {m.uuid.hex()}")
    print(f"size      : {len(m.raw)} bytes")
    print("fields:")
    for f in m.fields:
        scope = f" scope={f.scope}" if f.scope else ""
        fn = funcs.get(f.function)
        known = "" if fn else "  [no descriptor!]"
        print(
            f"  {f.name:10s} function={f.function}{scope}  "
            f"cols={len(f.cols)} prs={len(f.prs)} data={f.data_size}B "
            f"digest={f.digest[:8]}…{known}"
        )


def cmd_dump(args):
    m, funcs, _ = _load(args.file, _descriptor_dir(args.descriptors), args.entry)
    tree = _decode_all(m, funcs)
    if args.function:
        key = next((k for k in tree if k.split(":")[0] == args.function), None)
        if key is None:
            sys.exit(f"error: no field with function {args.function!r}")
        tree = tree[key]
    json.dump(tree, sys.stdout, indent=2, ensure_ascii=False)
    print()


def cmd_get(args):
    m, funcs, _ = _load(args.file, _descriptor_dir(args.descriptors), args.entry)
    parts = _parse_path(args.path)
    # first segment selects the field by function name (scope-qualified ok)
    fname, _ = parts[0]
    f = next((x for x in m.fields if x.function == fname or f"{x.function}:{x.scope}" == fname), None)
    if f is None:
        sys.exit(f"error: no field {fname!r} in file")
    vals = codec.decode_field(funcs[f.function], f)
    print(json.dumps(_get_path(vals, parts[1:]), ensure_ascii=False))


def cmd_set(args):
    m, funcs, container = _load(args.file, _descriptor_dir(args.descriptors), args.entry)
    patches = {}
    for assign in args.assignments:
        if "=" not in assign:
            sys.exit(f"error: expected PATH=VALUE, got {assign!r}")
        pth, val = assign.split("=", 1)
        parts = _parse_path(pth)
        fname = parts[0][0]
        patches.setdefault(fname, []).append((parts[1:], _coerce(val)))

    for fname, plist in patches.items():
        f = next((x for x in m.fields if x.function == fname or f"{x.function}:{x.scope}" == fname), None)
        if f is None:
            sys.exit(f"error: no field {fname!r} in file")
        fn = funcs[f.function]
        sparse = {}
        for parts, val in plist:
            _merge(sparse, _sparse_from_path(parts, val))
        f.data = codec.encode_field(fn, f, sparse)

    out = args.output or args.file
    if container is not None:
        # patch the modified inner file back into its entry and repack
        name = args.entry or DEFAULT_ENTRY
        container.entry(name).payload = m.to_bytes()
        container.save(out)
    else:
        m.save(out)
    print(f"wrote {out}")


def cmd_extract(args):
    if not _is_dm3f(args.file):
        sys.exit("error: extract only applies to .dm3f project files")
    container = dm3f_mod.parse_file(args.file)
    outdir = Path(args.dir or ".")
    outdir.mkdir(parents=True, exist_ok=True)
    for e in container.entries:
        dest = outdir / e.filename
        dest.write_bytes(e.payload)
        print(f"  {e.filename}  ({len(e.payload)} bytes, slot {e.slot})")
    print(f"extracted {len(container.entries)} files to {outdir}")


def cmd_diff(args):
    ddir = _descriptor_dir(args.descriptors)
    ma, funcs, _ = _load(args.a, ddir, args.entry)
    mb, _, _ = _load(args.b, ddir, args.entry)
    ta, tb = _decode_all(ma, funcs), _decode_all(mb, funcs)

    def walk(a, b, path):
        if isinstance(a, dict) and isinstance(b, dict):
            for k in sorted(set(a) | set(b)):
                walk(a.get(k), b.get(k), f"{path}.{k}" if path else k)
        elif isinstance(a, list) and isinstance(b, list):
            for i, (xa, xb) in enumerate(zip(a, b)):
                walk(xa, xb, f"{path}[{i}]")
        elif a != b:
            print(f"{path}: {json.dumps(a, ensure_ascii=False)} -> {json.dumps(b, ensure_ascii=False)}")

    if args.function:
        ka = next((k for k in ta if k.split(":")[0] == args.function), None)
        kb = next((k for k in tb if k.split(":")[0] == args.function), None)
        walk(ta.get(ka), tb.get(kb), args.function)
    else:
        walk(ta, tb, "")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="dm3", description=__doc__.splitlines()[0])
    ap.add_argument("--descriptors", help="DM3 Editor Descriptor/ directory")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("info", help="show header and field summary")
    p.add_argument("file")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("dump", help="decode file to JSON")
    p.add_argument("file")
    p.add_argument("--function", "-f", help="only this function (e.g. Mixing)")
    p.add_argument("--entry", "-e", help=".dm3f embedded entry (default: current memory)")
    p.set_defaults(func=cmd_dump)

    p = sub.add_parser("get", help="read one value by path")
    p.add_argument("file")
    p.add_argument("path", help="e.g. 'SceneInfo.Info.Title' or 'Mixing.InputChannel[0].Label.Name'")
    p.add_argument("--entry", "-e", help=".dm3f embedded entry")
    p.set_defaults(func=cmd_get)

    p = sub.add_parser("set", help="set values and write the file")
    p.add_argument("file")
    p.add_argument("assignments", nargs="+", metavar="PATH=VALUE")
    p.add_argument("--output", "-o", help="write to this path instead of in place")
    p.add_argument("--entry", "-e", help=".dm3f embedded entry")
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("diff", help="diff two files' decoded values")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--function", "-f")
    p.add_argument("--entry", "-e", help=".dm3f embedded entry")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("extract", help="extract .dm3f embedded files")
    p.add_argument("file")
    p.add_argument("--dir", "-d", help="output directory (default: .)")
    p.set_defaults(func=cmd_extract)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
