# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

CLI tooling (`dm3`) to parse and write Yamaha DM3 digital mixer files: `.dm3s` scenes, `.dm3p` presets, `.dm3f` show files. Pure-stdlib Python (no runtime deps). The formats were reverse-engineered in this repo; the format documentation lives in the module docstrings of `mbdf.py` and `dm3f.py` — treat those docstrings as the spec.

## Commands

```bash
scripts/fetch_fixtures.sh                    # REQUIRED FIRST: fetch descriptor XMLs
                                             # + factory files from Yamaha's installer
PYTHONPATH=src python3 -m dm3tools.cli ...   # run the CLI without installing
pip install -e .                             # installs the `dm3` entry point
python3 scripts/verify_layout.py             # structural invariants sweep over fixtures
python3 scripts/explore_mbdf.py -v FILE      # tag-structure walker for format archaeology
scripts/dm3-editor.sh                        # launch DM3 Editor under Wine (the oracle)
```

There is no test suite yet; validation is the round-trip sweep: decode every field of every fixture file, re-encode, and require semantic equality (see the git history for the inline harness — 845/845 fields must pass). Any codec change should re-run that sweep before committing.

## Architecture

The pipeline is three layers, each usable independently:

1. **`mbdf.py`** — container layer. All DM3 files are `#YAMAHA MBDF` files: header + UUID, then `#MMS FIELD` sections. Each field carries an `MMSXLIT` block (function name + schema digest), a table of `COL0`/`PR` records (an embedded copy of the schema), then a packed little-endian data block. `MbdfFile.to_bytes()` patches data blocks back into the original byte image — everything outside them is preserved byte-for-byte.

2. **`descriptors.py` + `codec.py`** — schema layer. Yamaha's `mms_*.xml` files (from the DM3 Editor install, fetched into `fixtures/descriptors/`) declare the full parameter tree: names, C types, ranges, defaults, struct sizes. The data block is a packed struct laid out exactly as a depth-first walk of that tree; `codec.py` decodes/encodes generically from it. Preset files store *scoped subtrees* (scope `CH`/`MX`/`ST`/`FXBS`); the subtree root is resolved by matching the field's first embedded `COL0` record name against the descriptor tree (`codec.resolve_tree`).

3. **`dm3f.py`** — show-file layer. A `.dm3f` is an MBDF `ProjectFile` followed by `#FILE` entries (slot + filename + big-endian sizes + zlib payload) and a `#END` footer. Each payload decompresses to another complete MBDF file (console current memory, stored scenes, scene list, `.old` journal copies). Writing recompresses with zlib level 1.

`cli.py` ties it together; for `.dm3f` inputs it transparently operates on an embedded entry (`--entry`, default `CurrentBackupFile.bup` = the console's current memory) and repacks the container on `set`.

## Invariants to preserve

- **Never commit `fixtures/`** — descriptor XMLs and factory presets are Yamaha-copyrighted; they're gitignored and reproduced via `scripts/fetch_fixtures.sh`. The repo's legal posture (README "Legal note") depends on distributing none of Yamaha's material.
- **Minimal-touch writes**: `set` must only change the bytes for the values being set. String fields in factory files carry junk after the first NUL (Yamaha doesn't zero buffers) — preserve it in untouched fields; zero-pad only fields we write. Full-width strings have no NUL terminator.
- The descriptor directory is resolved via `--descriptors` flag → `DM3_DESCRIPTOR_DIR` env var → repo `fixtures/descriptors` fallback (`cli._descriptor_dir`).
- Multi-byte header numbers are little-endian inside MBDF fields but **big-endian in `#FILE` entry headers** — don't "fix" one to match the other.

## Ground truth / oracle

The DM3 Editor under Wine is the validation oracle: files written by this tool must load in it. It must be launched with its install directory as cwd (it resolves `Descriptor/*.xml` relative to cwd and null-derefs otherwise) — always use `scripts/dm3-editor.sh`. Wine prefix `~/.wine-dm3`, set to win10.

## Known issues

- One factory `PresetDante` file decodes 148 bytes short on its `Status`/`DNTP` field (older schema revision suspected) — parked, don't chase it into the main codec.
- Rewritten `.dm3f` files are slightly larger than editor-written ones (zlib level 1 ratio); harmless since sizes are explicit in the container.
- Physical-console USB load test still pending (editor load test passes).

## Upstream

Prior art: [netik/decode_dm3](https://github.com/netik/decode_dm3) (no code shared; findings contributed as their issue #1). This repo is MIT — keep it free of copied code from there (Apache-2.0) and of Yamaha assets.
