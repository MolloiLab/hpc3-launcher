#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Installer Creation Script - Generate installers for the HPC Management System
Supports:
- Windows: Inno Setup (.exe installer)
- macOS: pkg format (.pkg installer)
- Linux: deb/rpm format
"""

import os
import sys
import platform
import subprocess
import shutil
import tempfile
from pathlib import Path

def create_windows_installer():
    """Create Windows installer (using Inno Setup)"""
    print("Creating Windows installer...")
    
    # Check if Inno Setup is installed
    iscc_path = shutil.which("iscc")
    if not iscc_path:
        print("Error: Inno Setup compiler (ISCC.exe) not found")
        print("Please install Inno Setup: https://jrsoftware.org/isdl.php")
        return False
    
    # Create Inno Setup script
    iss_script = """
    #define MyAppName "HPC Management System"
    #define MyAppVersion "1.0.0"
    #define MyAppPublisher "HPC Team"
    #define MyAppURL "https://example.com"
    #define MyAppExeName "HpcManagementSystem.exe"

    [Setup]
    AppId={{B0D97E6C-3A25-4BA4-9F2E-7D5A1C14F1D7}
    AppName={#MyAppName}
    AppVersion={#MyAppVersion}
    AppPublisher={#MyAppPublisher}
    AppPublisherURL={#MyAppURL}
    AppSupportURL={#MyAppURL}
    AppUpdatesURL={#MyAppURL}
    DefaultDirName={autopf}\\{#MyAppName}
    DisableProgramGroupPage=yes
    LicenseFile=LICENSE
    OutputBaseFilename=HPC_Management_System_Installer
    Compression=lzma
    SolidCompression=yes
    WizardStyle=modern

    [Languages]
    Name: "english"; MessagesFile: "compiler:Default.isl"

    [Tasks]
    Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

    [Files]
    Source: "dist\\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
    Source: "dist\\docs\\*"; DestDir: "{app}\\docs"; Flags: ignoreversion recursesubdirs createallsubdirs
    Source: "LICENSE"; DestDir: "{app}"; Flags: ignoreversion
    Source: "NOTICE.txt"; DestDir: "{app}"; Flags: ignoreversion
    Source: "README.md"; DestDir: "{app}"; Flags: ignoreversion

    [Icons]
    Name: "{autoprograms}\\{#MyAppName}"; Filename: "{app}\\{#MyAppExeName}"
    Name: "{autodesktop}\\{#MyAppName}"; Filename: "{app}\\{#MyAppExeName}"; Tasks: desktopicon

    [Run]
    Filename: "{app}\\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
    """
    
    # Write Inno Setup script to temporary file
    with tempfile.NamedTemporaryFile(delete=False, suffix=".iss") as tmp_file:
        tmp_file.write(iss_script.encode("utf-8"))
        iss_file_path = tmp_file.name
    
    try:
        # Run Inno Setup compiler
        subprocess.check_call(["iscc", iss_file_path])
        print("Windows installer created successfully!")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Failed to create Windows installer: {e}")
        return False
    finally:
        # Delete temporary script file
        os.unlink(iss_file_path)

def create_macos_installer():
    """Create macOS installer (.pkg format)"""
    print("Creating macOS installer...")
    
    # Check if on macOS
    if platform.system() != "Darwin":
        print("Error: macOS installer must be created on a macOS system")
        return False
    
    # Create temporary working directory
    work_dir = Path("build/macos_pkg")
    app_dir = work_dir / "Applications/HpcManagementSystem.app"
    scripts_dir = work_dir / "scripts"
    resources_dir = work_dir / "resources"
    
    # Clean and create directory structure
    if work_dir.exists():
        shutil.rmtree(work_dir)
    
    os.makedirs(app_dir / "Contents/MacOS", exist_ok=True)
    os.makedirs(app_dir / "Contents/Resources", exist_ok=True)
    os.makedirs(scripts_dir, exist_ok=True)
    os.makedirs(resources_dir, exist_ok=True)
    
    # Copy executable and resources
    shutil.copy("dist/HpcManagementSystem", app_dir / "Contents/MacOS/")
    
    # Create Info.plist
    info_plist = """<?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
    <plist version="1.0">
    <dict>
        <key>CFBundleExecutable</key>
        <string>HpcManagementSystem</string>
        <key>CFBundleIconFile</key>
        <string>AppIcon.icns</string>
        <key>CFBundleIdentifier</key>
        <string>com.example.hpcmanagement</string>
        <key>CFBundleInfoDictionaryVersion</key>
        <string>6.0</string>
        <key>CFBundleName</key>
        <string>HPC Management System</string>
        <key>CFBundlePackageType</key>
        <string>APPL</string>
        <key>CFBundleShortVersionString</key>
        <string>1.0.0</string>
        <key>CFBundleVersion</key>
        <string>1</string>
        <key>NSHighResolutionCapable</key>
        <true/>
        <key>NSPrincipalClass</key>
        <string>NSApplication</string>
    </dict>
    </plist>
    """
    
    with open(app_dir / "Contents/Info.plist", "w") as f:
        f.write(info_plist)
    
    # Create installation script
    postinstall_script = """#!/bin/bash
    # Set executable permissions
    chmod +x /Applications/HpcManagementSystem.app/Contents/MacOS/HpcManagementSystem
    exit 0
    """
    
    with open(scripts_dir / "postinstall", "w") as f:
        f.write(postinstall_script)
    
    # Set script permissions
    os.chmod(scripts_dir / "postinstall", 0o755)
    
    # Copy README to resources
    shutil.copy("README.md", resources_dir / "README.md")
    shutil.copy("LICENSE", resources_dir / "LICENSE")
    shutil.copy("NOTICE.txt", resources_dir / "NOTICE.txt")
    
    # Build pkg package
    try:
        subprocess.check_call([
            "pkgbuild",
            "--root", str(work_dir),
            "--install-location", "/",
            "--scripts", str(scripts_dir),
            "--identifier", "com.example.hpcmanagement",
            "--version", "1.0.0",
            "HPC_Management_System-1.0.0.pkg"
        ])
        print("macOS installer created successfully!")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Failed to create macOS installer: {e}")
        return False

def create_linux_installer():
    """Create Linux installer (.deb format)"""
    print("Creating Linux installer...")
    
    # Check if on Linux
    if platform.system() != "Linux":
        print("Error: Linux installer must be created on a Linux system")
        return False
    
    # Check if fpm is installed
    if not shutil.which("fpm"):
        print("Error: fpm tool not found")
        print("Please install fpm: gem install fpm")
        return False
    
    # Create temporary working directory
    work_dir = Path("build/linux_deb")
    bin_dir = work_dir / "usr/local/bin"
    share_dir = work_dir / "usr/share/hpc-management"
    desktop_dir = work_dir / "usr/share/applications"
    
    # Clean and create directory structure
    if work_dir.exists():
        shutil.rmtree(work_dir)
    
    os.makedirs(bin_dir, exist_ok=True)
    os.makedirs(share_dir, exist_ok=True)
    os.makedirs(desktop_dir, exist_ok=True)
    
    # Copy executable and resources
    shutil.copy("dist/HpcManagementSystem", bin_dir / "hpc-management")
    os.chmod(bin_dir / "hpc-management", 0o755)
    
    # Copy documentation
    if os.path.exists("dist/docs"):
        shutil.copytree("dist/docs", share_dir / "docs")
    
    # Copy license files
    shutil.copy("LICENSE", share_dir / "LICENSE")
    shutil.copy("NOTICE.txt", share_dir / "NOTICE.txt")
    
    # Create .desktop file
    desktop_file = """[Desktop Entry]
    Name=HPC Management System
    Comment=HPC Cluster Management Desktop Application
    Exec=/usr/local/bin/hpc-management
    Terminal=false
    Type=Application
    Categories=Utility;
    """
    
    with open(desktop_dir / "hpc-management.desktop", "w") as f:
        f.write(desktop_file)
    
    # Use fpm to create .deb package
    try:
        subprocess.check_call([
            "fpm",
            "-s", "dir",
            "-t", "deb",
            "-n", "hpc-management",
            "-v", "1.0.0",
            "-C", str(work_dir),
            "--description", "HPC Cluster Management Desktop Application",
            "--maintainer", "HPC Team <example@example.com>",
            "--vendor", "HPC Team",
            "--license", "MIT",
            "--url", "https://example.com"
        ])
        print("Linux installer created successfully!")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Failed to create Linux installer: {e}")
        return False

def main():
    """Main function to run installer creation"""
    print("HPC Management System Installer Creator")
    print("======================================")
    
    # Determine operating system
    os_name = platform.system()
    print(f"Detected operating system: {os_name}")
    
    # Check for dist directory
    if not os.path.exists("dist"):
        print("Error: 'dist' directory not found. Run pyinstaller_build.py first.")
        return False
    
    # Create installer based on operating system
    if os_name == "Windows":
        return create_windows_installer()
    elif os_name == "Darwin":
        return create_macos_installer()
    elif os_name == "Linux":
        return create_linux_installer()
    else:
        print(f"Error: Unsupported operating system: {os_name}")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1) 