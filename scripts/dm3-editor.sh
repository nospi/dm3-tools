#!/usr/bin/env bash
# Launch Yamaha DM3 Editor under Wine.
# The editor MUST run with its install dir as cwd, or it fails to load
# Descriptor/mms_*.xml and crashes with a null deref.
set -euo pipefail

export WINEPREFIX="${WINEPREFIX:-$HOME/.wine-dm3}"
export WINEDEBUG="${WINEDEBUG:--all}"

DM3_DIR="$WINEPREFIX/drive_c/Program Files (x86)/Yamaha/DM3"

if [[ ! -f "$DM3_DIR/dm3_editor.exe" ]]; then
    echo "DM3 Editor not found at $DM3_DIR" >&2
    echo "Run scripts/fetch_fixtures.sh first (it also installs the editor)." >&2
    exit 1
fi

cd "$DM3_DIR"
exec wine 'C:\Program Files (x86)\Yamaha\DM3\dm3_editor.exe' "$@"
