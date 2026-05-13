#!/usr/bin/env bash
# Post-py2app fixups for Diburit.app.
#
# py2app bundles native-dylib-loading packages (sounddevice, soundfile)
# twice: once as readable .py source in Contents/Resources/lib/python3.X/
# (good) and once as compiled .pyc inside Contents/Resources/lib/python39.zip
# (BAD - Python's importer prefers the zipped .pyc, and the resulting
# __file__ resolves to a path inside the zip, which dlopen cannot read
# from). The fix is to strip the duplicates out of the zip so the
# uncompressed sources win on import.
#
# Safe to re-run: zip --delete returns non-zero if entries are already
# missing, which we tolerate.
set -u

APP="/Users/orbenozio/Diburit/dist/Diburit.app"
ZIP="$APP/Contents/Resources/lib/python39.zip"
LIB_DIR="$APP/Contents/Resources/lib/python3.9"
VENV_SP="/Users/orbenozio/Diburit/.venv/lib/python3.9/site-packages"

# Code-signing identity. py2app produces an ad-hoc signed bundle whose
# identity is the binary hash, so every rebuild looks like a different
# app to TCC (Accessibility / Microphone / AppleEvents permissions get
# revoked on each rebuild). Re-signing with a real Apple-issued cert
# gives the bundle a stable Team ID + Bundle ID identity, so TCC keeps
# the permissions across rebuilds. Override via:
#   DIBURIT_SIGN_IDENTITY="..." bash postbuild.sh
# or set to an empty string to skip signing (falls back to py2app's
# ad-hoc signature).
SIGN_IDENTITY="${DIBURIT_SIGN_IDENTITY-Apple Development: Or Benozio (493VVKYUJ4)}"

if [ ! -f "$ZIP" ]; then
    echo "[postbuild] $ZIP not found - did py2app actually run?" >&2
    exit 1
fi

# These modules dlopen sibling dylibs at import time via paths relative
# to their own __file__. If __file__ resolves inside python39.zip the
# dlopen path is unreadable. We copy the .py source out of the venv into
# the on-disk python3.9 folder so Python's importer picks up the version
# whose __file__ is a real filesystem path, then strip the duplicate
# .pyc out of the zip so the importer has only one candidate.
MODULES_TO_FOLDERIZE=(
    "sounddevice"
    "_sounddevice"
    "soundfile"
)

for mod in "${MODULES_TO_FOLDERIZE[@]}"; do
    src="$VENV_SP/$mod.py"
    if [ -f "$src" ] && [ ! -f "$LIB_DIR/$mod.py" ]; then
        cp "$src" "$LIB_DIR/$mod.py"
        echo "[postbuild] copied $mod.py to lib/python3.9/"
    fi
    if zip -d "$ZIP" "$mod.pyc" >/dev/null 2>&1; then
        echo "[postbuild] removed $mod.pyc from python39.zip"
    fi
done

if [ -n "$SIGN_IDENTITY" ]; then
    # --force: replace the existing (ad-hoc) signature
    # --deep:  re-sign every nested bundle and dylib
    # --timestamp=none: skip the Apple timestamp server (offline-safe)
    if codesign --sign "$SIGN_IDENTITY" --force --deep --timestamp=none "$APP" 2>&1; then
        echo "[postbuild] signed with: $SIGN_IDENTITY"
    else
        echo "[postbuild] WARN: codesign failed - bundle stays ad-hoc signed" >&2
    fi
else
    echo "[postbuild] DIBURIT_SIGN_IDENTITY is empty - skipping codesign"
fi

# Icon sanity check + cache bust. Even when py2app+iconutil produce a
# valid multi-resolution Diburit.icns, macOS keeps a per-bundle icon
# cache keyed by (path, mtime). Without bumping mtime + re-registering
# with LaunchServices, Finder and System Settings often keep showing the
# previous build's icon (or the generic .app icon if this is the first
# build to ship an icon). Order matters: verify presence first so we
# fail loudly if py2app skipped copying the .icns, then touch + lsregister.
ICON="$APP/Contents/Resources/Diburit.icns"
if [ ! -f "$ICON" ]; then
    echo "[postbuild] WARN: $ICON missing - bundle will show the generic app icon" >&2
else
    echo "[postbuild] icon present: $ICON ($(stat -f%z "$ICON") bytes)"
    # Bump bundle mtime so Finder/Dock notice and re-read the icon.
    touch "$APP"
    # Re-register with LaunchServices so System Settings (Login Items,
    # Privacy & Security permission rows, etc.) pick up the new icon
    # without needing a logout. -f forces re-registration even if the
    # bundle was already known.
    LSREG=/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Versions/A/Support/lsregister
    if [ -x "$LSREG" ]; then
        "$LSREG" -f "$APP" >/dev/null 2>&1 && \
            echo "[postbuild] re-registered with LaunchServices" || \
            echo "[postbuild] WARN: lsregister returned non-zero" >&2
    fi
fi

echo "[postbuild] done"
