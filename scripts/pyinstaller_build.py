#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
PyInstaller Build Script - Package HPC3 Launcher into a standalone executable
"""

import os
import sys
import platform
import subprocess
import shutil
import datetime

def build_app():
    """Use PyInstaller to build a standalone executable"""
    
    # Clean previous builds
    for dir_name in ['build', 'dist']:
        if os.path.exists(dir_name):
            print(f"Cleaning {dir_name} directory...")
            shutil.rmtree(dir_name)
    
    # Ensure PyInstaller is installed
    try:
        import PyInstaller
    except ImportError:
        print("Installing PyInstaller...")
        # Use python3 -m pip to ensure it works in various environments
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])
    
    # Determine the operating system
    os_name = platform.system()
    print(f"Detected operating system: {os_name}")
    
    # Ensure required files exist
    required_files = ['LICENSE', 'NOTICE.txt']
    for file in required_files:
        if not os.path.exists(file):
            print(f"WARNING: Required file {file} does not exist.")
    
    # Copy additional files to include in the package
    print("Starting application build...")
    
    # Build the application using PyInstaller
    spec_file = "HPC3-Launcher.spec"
    if not os.path.exists(spec_file):
        print(f"ERROR: Spec file {spec_file} not found!")
        return False
    
    # Execute PyInstaller
    pyinstaller_cmd = [sys.executable, "-m", "PyInstaller", spec_file, "--clean"]
    
    print(f"Executing command: {' '.join(pyinstaller_cmd)}")
    result = subprocess.run(pyinstaller_cmd)
    
    if result.returncode != 0:
        print("ERROR: PyInstaller build failed!")
        return False
    
    # Copy additional files to the distribution
    print("Copying additional files...")
    
    # Check if build was successful
    if os.path.exists('dist'):
        if os_name == 'Darwin':
            # macOS app package
            app_path = os.path.join('dist', 'HPC3-Launcher.app')
            if os.path.exists(app_path):
                print(f"Build complete! Application is located at {app_path}")
            else:
                print("ERROR: macOS app package not found in dist directory.")
                return False
        elif os_name == 'Windows':
            # Windows executable
            exe_path = os.path.join('dist', 'HPC3-Launcher')
            if os.path.exists(exe_path):
                print(f"Build complete! Application is located at {exe_path}")
            else:
                print("ERROR: Windows executable not found in dist directory.")
                return False
        else:
            # Linux executable
            exe_path = os.path.join('dist', 'HPC3-Launcher')
            if os.path.exists(exe_path):
                print(f"Build complete! Application is located at {exe_path}")
            else:
                print("ERROR: Linux executable not found in dist directory.")
                return False
        
        print("You can distribute the dist directory to users for installation.")
        return True
    else:
        print("ERROR: Build directory 'dist' not found!")
        return False

def create_version_file(version):
    """Create a version file to include in the build"""
    version_file = os.path.join("hpc3_launcher", "version.txt")
    with open(version_file, "w") as f:
        f.write(version)
    return version_file

def main():
    """Main entry point"""
    # Parse command line arguments
    if len(sys.argv) > 1:
        # Check for version flag
        if sys.argv[1] == "--version":
            # Get version from version.py or default
            try:
                from hpc3_launcher.modules.updater import VERSION
                print(VERSION)
                return 0
            except ImportError:
                print("0.0.1")  # Default version
                return 0
    
    # Build the application
    success = build_app()
    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main()) 