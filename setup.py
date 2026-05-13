"""py2app build script for Diburit.

Run from this directory inside the venv:
    python setup.py py2app

py2app produces a real .app bundle whose Contents/MacOS/Diburit is a
proper Mach-O executable that embeds the Python interpreter. macOS TCC
sees the running process as Diburit.app (CFBundleIdentifier
com.orbenozio.diburit) - not as the Xcode developer Python that bit us
on SayIt. That stable identity is the whole reason this app exists as a
py2app build instead of a shell-script-wrapped .app.
"""

import re
from pathlib import Path

from setuptools import setup

APP = ["diburit.py"]
DATA_FILES = []

# Single source of truth for the app version: parse __version__ out of
# diburit.py instead of duplicating the literal here. Keeps CFBundleVersion
# and the runtime __version__ in lockstep so a tagged release cannot ship
# with mismatched numbers in About-this-app vs. logs.
_VERSION_RE = re.compile(r'^__version__\s*=\s*[\'"]([^\'"]+)[\'"]', re.MULTILINE)
_version_match = _VERSION_RE.search(Path("diburit.py").read_text(encoding="utf-8"))
if not _version_match:
    raise RuntimeError("could not find __version__ in diburit.py")
VERSION = _version_match.group(1)

# Resolve the .icns next to setup.py. py2app accepts either an absolute
# path or a path relative to the script's CWD.
_ICON_CANDIDATE = Path("Diburit.icns")
ICONFILE = str(_ICON_CANDIDATE.resolve()) if _ICON_CANDIDATE.exists() else None

PLIST = {
    "CFBundleName": "Diburit",
    "CFBundleDisplayName": "Diburit",
    "CFBundleIdentifier": "com.orbenozio.diburit",
    "CFBundleVersion": VERSION,
    "CFBundleShortVersionString": VERSION,
    "CFBundleExecutable": "Diburit",
    "CFBundleInfoDictionaryVersion": "6.0",
    "CFBundlePackageType": "APPL",
    # Menu-bar agent: no Dock icon, no main window.
    "LSUIElement": True,
    # TCC usage strings - macOS shows these in the permission prompts and
    # silently denies the request without showing the dialog if any are
    # missing. Triple-check that all three remain set if anything else is
    # tweaked in this dict.
    "NSMicrophoneUsageDescription":
        "Diburit records your voice for Hebrew speech-to-text.",
    "NSAppleEventsUsageDescription":
        "Diburit activates the focused window and inspects its name to "
        "decide where to paste the transcribed text.",
    # CGEventPost (used for Cmd+V) is gated by Accessibility, which is
    # granted via System Settings and does not have a usage-description
    # key, so nothing else is needed here for the paste path.
    "NSHighResolutionCapable": True,
    "LSMinimumSystemVersion": "12.0",
}

OPTIONS = {
    "argv_emulation": False,
    "plist": PLIST,
    # `packages` here means "keep this package as an uncompressed folder
    # under Contents/Resources/lib/python3.x/" instead of inside the
    # site-packages.zip. That matters for any package whose own code
    # dlopens a sibling dylib at runtime - dlopen cannot load from
    # within a zip. Both sounddevice and soundfile do this:
    #   _sounddevice_data/portaudio-binaries/libportaudio.dylib
    #   _soundfile_data/libsndfile_arm64.dylib
    "packages": [
        "rumps",
        "numpy",
        "sounddevice",
        "soundfile",
        "_sounddevice_data",
        "_soundfile_data",
        "requests",
        "dotenv",
        "Quartz",
        "AppKit",
        "Foundation",
        "AVFoundation",
        # Edge TTS pulls in an asyncio + aiohttp stack. Listed explicitly
        # so py2app keeps the packages as folders (their websocket + SSL
        # code resolves data files at runtime that don't survive zipping).
        "edge_tts",
        "aiohttp",
        "certifi",
        # gTTS (Google Translate TTS) - secondary remote TTS backend.
        # Pulls in click + soupsieve + bs4 as transitive deps.
        "gtts",
    ],
    "includes": [
        "cffi",
    ],
    # Native dylibs sounddevice / soundfile depend on. py2app drops them
    # into Contents/Frameworks/ where dlopen will find them.
    "frameworks": [
        "/Users/orbenozio/Diburit/.venv/lib/python3.9/site-packages/_sounddevice_data/portaudio-binaries/libportaudio.dylib",
        "/Users/orbenozio/Diburit/.venv/lib/python3.9/site-packages/_soundfile_data/libsndfile_arm64.dylib",
    ],
    "iconfile": ICONFILE,
}

setup(
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
