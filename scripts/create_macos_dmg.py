#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
macOS DMG Creator for HPC3 Launcher
Creates a DMG installer package for macOS
"""

import os
import sys
import platform
import subprocess
import shutil
import tempfile
from pathlib import Path
import plistlib

# Get paths
SCRIPT_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
PROJECT_ROOT = SCRIPT_DIR.parent

# Debug prints
print(f"__file__: {__file__}")
print(f"SCRIPT_DIR: {SCRIPT_DIR}")
print(f"PROJECT_ROOT: {PROJECT_ROOT}")

# Application constants
APP_NAME = "HPC3-Launcher"
APP_BUNDLE_NAME = f"{APP_NAME}.app"
APP_IDENTIFIER = "edu.uci.hpc3launcher"
APP_VERSION = "0.0.1"  # This should match the version in updater.py
DIST_DIR = PROJECT_ROOT / "dist"
DMG_NAME = f"{APP_NAME}-{APP_VERSION}-macos.dmg"
OUTPUT_DMG = PROJECT_ROOT / DMG_NAME

# More debug prints
print(f"DIST_DIR: {DIST_DIR}")
print(f"DIST_DIR exists: {DIST_DIR.exists()}")
print(f"App bundle path: {DIST_DIR / APP_BUNDLE_NAME}")
print(f"App bundle exists: {(DIST_DIR / APP_BUNDLE_NAME).exists()}")

def create_macos_dmg():
    """Create macOS DMG installer"""
    print(f"Creating macOS DMG installer for {APP_NAME}...")
    
    # Check if on macOS
    if platform.system() != "Darwin":
        print("Error: macOS DMG must be created on a macOS system")
        return False
    
    # Check if the app bundle exists
    app_bundle_path = DIST_DIR / APP_BUNDLE_NAME
    if not app_bundle_path.exists():
        print(f"Error: {APP_BUNDLE_NAME} not found in {DIST_DIR}")
        print("Run PyInstaller first to create the app bundle")
        return False
    
    # Create DMG build directory
    dmg_build_dir = PROJECT_ROOT / "build/dmg"
    if dmg_build_dir.exists():
        shutil.rmtree(dmg_build_dir)
    os.makedirs(dmg_build_dir, exist_ok=True)
    
    # Copy app bundle to the DMG build directory
    dmg_app_path = dmg_build_dir / APP_BUNDLE_NAME
    print(f"Copying {app_bundle_path} to {dmg_app_path}...")
    shutil.copytree(app_bundle_path, dmg_app_path)

    # Create an Applications symlink
    applications_link = dmg_build_dir / "Applications"
    print("Creating Applications symlink...")
    os.symlink("/Applications", applications_link)
    
    # Create DMG background directory
    background_dir = dmg_build_dir / ".background"
    os.makedirs(background_dir, exist_ok=True)
    
    # Check if we have a background image
    background_image = PROJECT_ROOT / "hpc3_launcher/resources/dmg_background.png"
    if background_image.exists():
        shutil.copy(background_image, background_dir / "background.png")
        has_background = True
    else:
        print("No background image found, using plain DMG...")
        has_background = False
    
    # Create DMG file
    print(f"Creating DMG file: {DMG_NAME}...")
    
    # First create a temporary DMG
    temp_dmg = Path(tempfile.gettempdir()) / f"{APP_NAME}-temp.dmg"
    if temp_dmg.exists():
        os.unlink(temp_dmg)
    
    # Create temporary DMG
    try:
        subprocess.check_call([
            "hdiutil", "create",
            "-srcfolder", str(dmg_build_dir),
            "-volname", APP_NAME,
            "-fs", "HFS+",
            "-fsargs", "-c c=64,a=16,e=16",
            "-format", "UDRW",
            str(temp_dmg)
        ])
    except subprocess.CalledProcessError as e:
        print(f"Failed to create temporary DMG: {e}")
        return False
    
    # Mount the temporary DMG
    mount_point = Path("/Volumes") / APP_NAME
    if mount_point.exists():
        subprocess.call(["hdiutil", "detach", str(mount_point), "-force"])
    
    try:
        subprocess.check_call([
            "hdiutil", "attach",
            "-readwrite", 
            "-noverify",
            str(temp_dmg)
        ])
    except subprocess.CalledProcessError as e:
        print(f"Failed to mount temporary DMG: {e}")
        return False
    
    # Set DMG appearance
    if has_background:
        # Create .DS_Store with background and icon arrangement
        # This is done via an AppleScript
        applescript = f"""
        tell application "Finder"
            tell disk "{APP_NAME}"
                open
                set current view of container window to icon view
                set toolbar visible of container window to false
                set statusbar visible of container window to false
                set the bounds of container window to {{100, 100, 600, 450}}
                set viewOptions to the icon view options of container window
                set arrangement of viewOptions to not arranged
                set icon size of viewOptions to 128
                set background picture of viewOptions to file ".background:background.png"
                set position of item "{APP_BUNDLE_NAME}" of container window to {{140, 200}}
                set position of item "Applications" of container window to {{400, 200}}
                update without registering applications
                delay 5
                close
            end tell
        end tell
        """
        
        with tempfile.NamedTemporaryFile(delete=False, suffix=".applescript") as tmp_file:
            tmp_file.write(applescript.encode("utf-8"))
            applescript_path = tmp_file.name
        
        try:
            subprocess.check_call(["osascript", applescript_path])
        except subprocess.CalledProcessError as e:
            print(f"Warning: Failed to set DMG appearance: {e}")
        finally:
            os.unlink(applescript_path)
    
    # Unmount the temporary DMG
    try:
        subprocess.check_call(["hdiutil", "detach", str(mount_point)])
    except subprocess.CalledProcessError:
        print("Warning: Failed to unmount DMG properly")
        try:
            subprocess.call(["hdiutil", "detach", str(mount_point), "-force"])
        except:
            print("Error: Could not unmount DMG even with force option")
            return False
    
    # Create the final compressed DMG
    final_dmg = OUTPUT_DMG
    if final_dmg.exists():
        os.unlink(final_dmg)
    
    try:
        subprocess.check_call([
            "hdiutil", "convert",
            str(temp_dmg),
            "-format", "UDZO",
            "-o", str(final_dmg)
        ])
        
        # Delete the temporary DMG
        os.unlink(temp_dmg)
        
        print(f"DMG created successfully: {final_dmg}")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Failed to create final DMG: {e}")
        return False

def update_info_plist():
    """Update the Info.plist file with correct bundle information"""
    print("Updating Info.plist...")
    
    app_bundle_path = DIST_DIR / APP_BUNDLE_NAME
    info_plist_path = app_bundle_path / "Contents/Info.plist"
    
    if not info_plist_path.exists():
        print(f"Error: Info.plist not found at {info_plist_path}")
        return False
    
    try:
        # Read the existing plist
        with open(info_plist_path, 'rb') as fp:
            plist = plistlib.load(fp)
        
        # Update values
        plist['CFBundleIdentifier'] = APP_IDENTIFIER
        plist['CFBundleName'] = APP_NAME
        plist['CFBundleDisplayName'] = APP_NAME
        plist['CFBundleShortVersionString'] = APP_VERSION
        plist['CFBundleVersion'] = APP_VERSION
        
        # Write back the updated plist
        with open(info_plist_path, 'wb') as fp:
            plistlib.dump(plist, fp)
        
        print("Info.plist updated successfully")
        return True
    except Exception as e:
        print(f"Error updating Info.plist: {e}")
        return False

def main():
    """Main function to run DMG creation"""
    print("HPC3 Launcher DMG Creator")
    print("==============================")
    
    # Check if on macOS
    if platform.system() != "Darwin":
        print("Error: This script must run on macOS")
        return False
    
    # Update Info.plist in the app bundle
    if not update_info_plist():
        print("Warning: Failed to update Info.plist, continuing with DMG creation")
    
    # Create DMG
    return create_macos_dmg()

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1) 