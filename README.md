# dm3-tools

CLI tooling to parse and write **Yamaha DM3** digital mixer files — scenes,
presets, and full show files, for both the console's USB save/load and the
DM3 Editor. Verified end-to-end: files written by `dm3 set` load cleanly in
Yamaha's DM3 Editor.

## File types

| Extension | Contents | Status |
|-----------|----------|--------|
| `.dm3s` | One scene (SceneInfo + Mixing + Processing + FX) | read/write |
| `.dm3p` | One library preset (scoped subtree: CH/MX/ST/FXBS) | read/write |
| `.dm3f` | All mixer settings ("show file", USB ↔ console ↔ editor) | read/write |

## Usage

```bash
dm3 info  scene.dm3s                          # header + field summary
dm3 dump  scene.dm3s -f Mixing                # decode to JSON
dm3 get   scene.dm3s SceneInfo.Info.Title
dm3 set   scene.dm3s "SceneInfo.Info.Title=FSC Town Hall" -o out.dm3s
dm3 set   scene.dm3s "Mixing.InputChannel[0].Label.Name=Kick"
dm3 diff  before.dm3s after.dm3s              # what changed between saves

# .dm3f show files: --entry addresses embedded files
# (default entry is the console's current memory)
dm3 info    show.dm3f                         # list embedded files
dm3 set     show.dm3f "Mixing.InputChannel[0].Label.Name=KICK IN" -o out.dm3f
dm3 dump    show.dm3f -e 88112F6B….dm3s       # decode a stored scene
dm3 extract show.dm3f -d out/                 # unpack all embedded files
```

Only the bytes for values you set are touched; everything else in the file is
preserved byte-for-byte.

## How it works

DM3 files are `#YAMAHA MBDF` containers: a header + UUID, then `#MMS FIELD`
sections. Each field carries an `MMSXLIT` schema block (function name + digest
pinning the schema version), a table of `COL0` (collection) and `PR`
(parameter) records describing the parameter tree, then a packed
little-endian data block laid out exactly as the tree declares.

The authoritative schema ships with Yamaha's own DM3 Editor as
`Descriptor/mms_*.xml` (names, C types, ranges, defaults, struct sizes).
`dm3tools` parses those XMLs and drives all decode/encode from them —
no per-parameter reverse engineering. Validated by round-tripping all 278
factory presets/scenes bundled with the editor (845/845 fields).

### Getting the descriptors & fixtures

Yamaha's descriptor XMLs and factory presets are copyrighted, so they aren't
committed here. Fetch them from Yamaha's public DM3 Editor installer:

```bash
scripts/fetch_fixtures.sh   # downloads editor, extracts via msiextract
```

Or point the CLI at an existing DM3 Editor install:

```bash
export DM3_DESCRIPTOR_DIR="/path/to/Yamaha/DM3/Descriptor"
```

## Running DM3 Editor on Linux

The Windows editor runs under Wine (tested wine 6.0.3, prefix set to win10).
It must be started with its install directory as the working directory or it
crashes on launch (it loads `Descriptor/mms_*.xml` relative to cwd):

```bash
scripts/dm3-editor.sh
```

### .dm3f show files

A `.dm3f` is an MBDF file of type `ProjectFile`: a ProjectInfo field, then a
series of `#FILE` entries — slot name (`Current`, `Scene:A`, `SceneList`),
big-endian sizes, filename, and a **zlib** payload that decompresses to
another MBDF file (the console's current memory, each stored scene, the
scene-list index, plus `.old` journal copies). See `src/dm3tools/dm3f.py`
for the exact byte layout.

## Prior art & credits

[netik/decode_dm3](https://github.com/netik/decode_dm3) (Apache-2.0) did the
first public spelunking of these formats and correctly identified the MBDF
container markers. dm3-tools shares no code with it, but their work is what
made it clear the formats were tractable. Their open question — the
compression on `.dm3f` embedded files — turns out to be plain zlib.

## Legal note

dm3-tools is an independent interoperability project, not affiliated with or
endorsed by Yamaha. It was built by observing files the DM3 console and DM3
Editor produce; no Yamaha software was decompiled or disassembled, and no
Yamaha-copyrighted material (descriptor XMLs, factory presets, binaries) is
distributed in this repository — `scripts/fetch_fixtures.sh` obtains those
from Yamaha's own public installer. Yamaha, DM3 and related marks belong to
Yamaha Corporation.

## Notes / known issues

- Physical-console USB load test pending (editor load test passes).
- One factory file (`PresetDante` type, Status/DNTP field) decodes 148 bytes
  short of its descriptor — different schema revision suspected. Parked.
- String fields in factory files contain junk after the NUL terminator
  (Yamaha doesn't zero buffers). Semantically irrelevant; we preserve it for
  untouched fields and zero-pad fields we write.
- Rewritten `.dm3f` files are slightly larger than editor-written ones (our
  zlib level 1 is less tight than Yamaha's); harmless, sizes are explicit in
  the container.

## Development

```bash
pip install -e .
PYTHONPATH=src python3 -m dm3tools.cli ...   # without installing
python3 scripts/verify_layout.py             # structural invariants sweep
```
