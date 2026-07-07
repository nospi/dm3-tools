# dm3-tools

CLI tooling to parse and write **Yamaha DM3** digital mixer files — scenes,
presets, and (in progress) full show files, for both the console's USB
save/load and the DM3 Editor.

## File types

| Extension | Contents | Status |
|-----------|----------|--------|
| `.dm3s` | One scene (SceneInfo + Mixing + Processing + FX) | read/write |
| `.dm3p` | One library preset (scoped subtree: CH/MX/ST/FXBS) | read/write |
| `.dm3f` | All mixer settings ("show file", USB ↔ console ↔ editor) | in progress |

## Usage

```bash
dm3 info  scene.dm3s                          # header + field summary
dm3 dump  scene.dm3s -f Mixing                # decode to JSON
dm3 get   scene.dm3s SceneInfo.Info.Title
dm3 set   scene.dm3s "SceneInfo.Info.Title=FSC Town Hall" -o out.dm3s
dm3 set   scene.dm3s "Mixing.InputChannel[0].Label.Name=Kick"
dm3 diff  before.dm3s after.dm3s              # what changed between saves
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

## Notes / known issues

- One factory file (`PresetDante` type, Status/DNTP field) decodes 148 bytes
  short of its descriptor — different schema revision suspected. Parked.
- String fields in factory files contain junk after the NUL terminator
  (Yamaha doesn't zero buffers). Semantically irrelevant; we preserve it for
  untouched fields and zero-pad fields we write.
- `.dm3f` container layout differs (embeds many objects + possible
  compression); needs a real sample from the editor/console to finish.
  See also [netik/decode_dm3](https://github.com/netik/decode_dm3) (Apache-2.0)
  for prior art on the container.

## Development

```bash
pip install -e .
PYTHONPATH=src python3 -m dm3tools.cli ...   # without installing
python3 scripts/verify_layout.py             # structural invariants sweep
```
