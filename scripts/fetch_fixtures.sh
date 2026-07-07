#!/usr/bin/env bash
# Fetch Yamaha DM3 Editor and extract descriptor XMLs + factory presets into
# fixtures/. These files are Yamaha-copyrighted and are not committed to git.
#
# Requires: curl, unzip, msiextract (msitools), wine (only to unpack the
# Inno bootstrapper's embedded MSI).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

URL="https://usa.yamaha.com/files/download/software/5/2345725/dm3_edt300_win.zip"

echo "-- downloading DM3 Editor V3.0.0 (~25MB)"
curl -sL -o "$WORK/dm3_editor.zip" "$URL"
unzip -q "$WORK/dm3_editor.zip" -d "$WORK"

echo "-- unpacking Inno bootstrapper to reach the embedded MSI"
if command -v innoextract >/dev/null; then
    innoextract -s -d "$WORK/inno" "$WORK/setup.exe"
    MSI_DIR="$(dirname "$(find "$WORK/inno" -name 'DM3Editor.msi' | head -1)")"
else
    # innoextract missing: run the installer under a throwaway wine prefix and
    # snatch the MSI + cab from its temp dir mid-install.
    export WINEPREFIX="$WORK/wineprefix" WINEDEBUG=-all
    wineboot -i >/dev/null 2>&1
    winecfg -v win10 >/dev/null 2>&1
    cp "$WORK/setup.exe" "$WINEPREFIX/drive_c/"
    (timeout 60 wine 'C:\setup.exe' /VERYSILENT /SUPPRESSMSGBOXES /NORESTART >/dev/null 2>&1 || true) &
    MSI_DIR=""
    for _ in $(seq 1 40); do
        CAB=$(find "$WINEPREFIX/drive_c/users" -name 'DM3Edt.cab' 2>/dev/null | head -1)
        if [[ -n "$CAB" ]]; then
            sleep 2
            MSI_DIR="$WORK/payload"
            cp -r "$(dirname "$CAB")" "$MSI_DIR"
            break
        fi
        sleep 1
    done
    wineserver -k 2>/dev/null || true
    [[ -n "$MSI_DIR" ]] || { echo "failed to capture MSI payload" >&2; exit 1; }
fi

echo "-- extracting files from MSI"
msiextract -C "$WORK/files" "$MSI_DIR/DM3Editor.msi" >/dev/null

SRC="$WORK/files/Program Files/Yamaha/DM3"
mkdir -p "$ROOT/fixtures"
rm -rf "$ROOT/fixtures/descriptors" "$ROOT/fixtures/factory"
cp -r "$SRC/Descriptor" "$ROOT/fixtures/descriptors"
cp -r "$SRC/FactoryPreset" "$ROOT/fixtures/factory"

echo "-- done:"
echo "   $(ls "$ROOT/fixtures/descriptors" | wc -l) descriptor XMLs"
echo "   $(find "$ROOT/fixtures/factory" -name '*.dm3p' | wc -l) factory presets"
echo "   $(find "$ROOT/fixtures/factory" -name '*.dm3s' | wc -l) factory scenes"

# optional: also install the editor into the dm3 wine prefix for GUI use
if [[ "${INSTALL_EDITOR:-}" == "1" ]]; then
    export WINEPREFIX="$HOME/.wine-dm3"
    mkdir -p "$WINEPREFIX/drive_c/Program Files (x86)/Yamaha"
    cp -r "$SRC" "$WINEPREFIX/drive_c/Program Files (x86)/Yamaha/DM3"
    echo "-- editor installed into $WINEPREFIX (launch with scripts/dm3-editor.sh)"
fi
