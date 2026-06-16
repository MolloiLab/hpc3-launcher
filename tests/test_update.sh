#!/bin/bash
# Script for testing the auto-update functionality

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}[INFO]${NC} Testing HPC3 Launcher update system"
echo -e "${BLUE}[INFO]${NC} This script will set up a local mock for testing updates from v0.0.1 to v0.0.2"

# Create local test directory 
TEST_DIR="./update_test"
mkdir -p "$TEST_DIR"
mkdir -p "$TEST_DIR/releases/download/v0.0.2"
mkdir -p "$TEST_DIR/tmp"

# Copy release notes
echo -e "${BLUE}[INFO]${NC} Preparing release notes..."
cp test_v0.0.2/RELEASE_NOTES.md "$TEST_DIR/release_notes.md"

# Create mock dmg file for testing
echo -e "${BLUE}[INFO]${NC} Creating mock DMG installer..."
touch "$TEST_DIR/releases/download/v0.0.2/HPC3-Launcher-v0.0.2-macos.dmg"

# Create fake GitHub API response JSON with local file URLs
echo -e "${BLUE}[INFO]${NC} Creating mock GitHub API response..."
CURRENT_DIR=$(pwd)
cat > "$TEST_DIR/latest_release.json" << EOL
{
  "tag_name": "v0.0.2",
  "name": "HPC3 Launcher v0.0.2",
  "body": $(cat "$TEST_DIR/release_notes.md" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))'),
  "assets": [
    {
      "name": "HPC3-Launcher-v0.0.2-macos.dmg",
      "browser_download_url": "file://$CURRENT_DIR/$TEST_DIR/releases/download/v0.0.2/HPC3-Launcher-v0.0.2-macos.dmg"
    },
    {
      "name": "HPC3-Launcher-v0.0.2-win64.exe",
      "browser_download_url": "file://$CURRENT_DIR/$TEST_DIR/releases/download/v0.0.2/HPC3-Launcher-v0.0.2-win64.exe"
    },
    {
      "name": "hpc3-launcher_0.0.2_amd64.deb",
      "browser_download_url": "file://$CURRENT_DIR/$TEST_DIR/releases/download/v0.0.2/hpc3-launcher_0.0.2_amd64.deb"
    }
  ],
  "published_at": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
}
EOL

# Create patch for updater.py to use local mock API
echo -e "${BLUE}[INFO]${NC} Creating patch for updater.py..."
cat > "$TEST_DIR/updater_patch.py" << EOL
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Patched updater module for testing
"""

import os
import sys
import json
import platform
import tempfile
import shutil
import logging
import subprocess
import requests
from PyQt5.QtCore import QObject, pyqtSignal, QThread
from packaging import version

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Constants
VERSION = "0.0.1"  # Current version
GITHUB_REPO = "songliangyu/UCI-ClusterManager"  # Update this with your actual GitHub repository
GITHUB_API_URL = "file://$(pwd)/$TEST_DIR/latest_release.json"  # Local mock API
UPDATE_CHECK_INTERVAL = 24 * 60 * 60  # Check every 24 hours (in seconds)
EOL

# Build patched updater.py
echo -e "${BLUE}[INFO]${NC} Building patched updater.py..."
cat "$TEST_DIR/updater_patch.py" > "$TEST_DIR/updater_patch_header.py"
tail -n +17 hpc3_launcher/modules/updater.py >> "$TEST_DIR/updater.py.patched"
cat "$TEST_DIR/updater_patch_header.py" "$TEST_DIR/updater.py.patched" > "$TEST_DIR/tmp/updater.py"

# Instructions for testing
echo -e "${GREEN}[SUCCESS]${NC} Setup complete!"
echo -e "${YELLOW}[INSTRUCTIONS]${NC} To test the update functionality:"
echo "1. Back up your original updater.py:"
echo "   cp hpc3_launcher/modules/updater.py hpc3_launcher/modules/updater.py.bak"
echo ""
echo "2. Copy the patched updater.py to your project:"
echo "   cp $TEST_DIR/tmp/updater.py hpc3_launcher/modules/updater.py"
echo ""
echo "3. Run your application, it should detect version 0.0.2 as available"
echo "   python hpc3_launcher/main.py"
echo ""
echo "4. After testing, restore your original updater.py:"
echo "   cp hpc3_launcher/modules/updater.py.bak hpc3_launcher/modules/updater.py"
echo ""
echo -e "${BLUE}[INFO]${NC} The mock server will make the application think 0.0.2 is available"
echo -e "${BLUE}[INFO]${NC} When you click 'Download Update', it will download a empty file for demonstration"

# Make script executable
chmod +x "$TEST_DIR/tmp/updater.py" 