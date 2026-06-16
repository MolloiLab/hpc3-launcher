#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build a clean, compressed macOS .dmg from dist/HPC3-Launcher.app.

Layout inside the DMG: the app bundle next to an /Applications symlink, so the
user just drags the app onto Applications. No Finder AppleScript styling (that
needs a GUI session and fails on headless CI runners) -- just a reliable
hdiutil UDZO image. Version comes from the package, so it always matches the app.

Usage:
    python create_dmg.py            # -> HPC3-Launcher-<version>-macos.dmg
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
APP_NAME = "HPC3-Launcher"
APP_BUNDLE = f"{APP_NAME}.app"
DIST_DIR = PROJECT_ROOT / "dist"


def app_version():
    """Read VERSION from the package so the DMG name matches the app."""
    sys.path.insert(0, str(PROJECT_ROOT / "hpc3_launcher"))
    try:
        from modules.updater import VERSION
        return VERSION
    except Exception:
        return "0.0.0"


def create_dmg():
    app_path = DIST_DIR / APP_BUNDLE
    if not app_path.exists():
        print(f"ERROR: app bundle not found: {app_path}")
        return None

    version = app_version()
    output_dmg = PROJECT_ROOT / f"{APP_NAME}-{version}-macos.dmg"
    staging = PROJECT_ROOT / "build" / "dmg"
    temp_dmg = PROJECT_ROOT / f"{APP_NAME}-temp.dmg"

    # Fresh staging dir with the app + an Applications symlink.
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    print("Copying app into staging…")
    subprocess.run(["cp", "-R", str(app_path), str(staging / APP_BUNDLE)], check=True)
    os.symlink("/Applications", staging / "Applications")

    for path in (temp_dmg, output_dmg):
        if path.exists():
            path.unlink()

    print("Creating image…")
    subprocess.run([
        "hdiutil", "create", "-volname", "HPC3 Launcher",
        "-srcfolder", str(staging), "-ov", "-format", "UDRW", str(temp_dmg),
    ], check=True)

    print(f"Compressing -> {output_dmg.name}")
    subprocess.run([
        "hdiutil", "convert", str(temp_dmg), "-format", "UDZO", "-o", str(output_dmg),
    ], check=True)
    temp_dmg.unlink(missing_ok=True)

    print(f"DMG created: {output_dmg}")
    return output_dmg


if __name__ == "__main__":
    dmg = create_dmg()
    sys.exit(0 if dmg else 1)
