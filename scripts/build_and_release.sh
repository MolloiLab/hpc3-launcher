#!/bin/bash
# Build and release script for HPC3 Launcher
# This script builds the application, creates a DMG installer, and prepares it for GitHub release

set -e  # Exit on error

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Get script directory and project root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"

# Change to project root directory
cd "$PROJECT_ROOT"
echo -e "${BLUE}Working directory: $(pwd)${NC}"

# 激活conda环境
echo -e "${BLUE}激活conda环境hpc_env...${NC}"
source $(conda info --base)/etc/profile.d/conda.sh
conda activate hpc_env
echo -e "${BLUE}Python路径: $(which python)${NC}"
echo -e "${BLUE}Python版本: $(python --version)${NC}"

# Get the version from updater.py
VERSION=$(grep "VERSION = " "$PROJECT_ROOT/hpc3_launcher/modules/updater.py" | cut -d'"' -f2)
if [ -z "$VERSION" ]; then
  echo -e "${RED}Error: Could not determine version from updater.py${NC}"
  exit 1
fi

echo -e "${BLUE}Building HPC3 Launcher version ${VERSION}${NC}"

# Determine OS
OS=$(uname)

# Clean up previous builds
echo -e "${BLUE}Cleaning up previous builds...${NC}"
rm -rf build dist *.dmg *.pkg *.exe *.deb

# Build the application
echo -e "${BLUE}Building application with PyInstaller...${NC}"
python -m PyInstaller "$PROJECT_ROOT/scripts/HPC3-Launcher.spec"

# Create platform-specific installer
if [ "$OS" == "Darwin" ]; then
  echo -e "${BLUE}Creating macOS DMG installer...${NC}"
  python "$PROJECT_ROOT/scripts/create_macos_dmg.py"
  
  if [ ! -f "HPC3-Launcher-${VERSION}-macos.dmg" ]; then
    echo -e "${RED}Error: DMG creation failed${NC}"
    exit 1
  fi

  echo -e "${GREEN}DMG created: HPC3-Launcher-${VERSION}-macos.dmg${NC}"
elif [ "$OS" == "Linux" ]; then
  echo -e "${YELLOW}Linux installer creation not implemented in this script${NC}"
  echo -e "${YELLOW}Please use existing tools to create a .deb package${NC}"
else
  echo -e "${YELLOW}Windows installer creation not implemented in this script${NC}"
  echo -e "${YELLOW}Please use existing tools to create a Windows installer${NC}"
fi

# Create release notes if they don't exist
RELEASE_NOTES="RELEASE_NOTES_${VERSION}.md"
if [ ! -f "$RELEASE_NOTES" ]; then
  echo -e "${BLUE}Creating template release notes...${NC}"
  cat > "$RELEASE_NOTES" << EOL
# HPC3 Launcher ${VERSION}

## New Features

- Auto-update functionality using GitHub Releases
- DMG installer for macOS
- Improved UI and branding for UCI

## Bug Fixes

- Fixed issue with application naming
- Improved error handling during updates

## Improvements

- Better organization of project files
- Streamlined build process

## System Requirements

- **Windows**: Windows 10 or later
- **macOS**: macOS 10.13 or later
- **Linux**: Ubuntu 18.04+, CentOS 7+, or similar systems
EOL
  echo -e "${YELLOW}Please edit ${RELEASE_NOTES} with actual release notes${NC}"
fi

# Instructions for creating a GitHub release
echo -e "${GREEN}Build completed successfully!${NC}"
echo -e "${BLUE}To create a GitHub release:${NC}"
echo -e "1. Edit the release notes in ${RELEASE_NOTES}"
echo -e "2. Create a tag: ${YELLOW}git tag v${VERSION}${NC}"
echo -e "3. Push the tag: ${YELLOW}git push origin v${VERSION}${NC}"
echo -e "4. Create a new release on GitHub: ${YELLOW}https://github.com/SmallNeon/UCI-ClusterManager/releases/new${NC}"
echo -e "   - Tag: v${VERSION}"
echo -e "   - Title: HPC3 Launcher ${VERSION}"
echo -e "   - Description: Copy from ${RELEASE_NOTES}"
echo -e "   - Attach the installer files:"
if [ "$OS" == "Darwin" ]; then
  echo -e "     - ${YELLOW}HPC3-Launcher-${VERSION}-macos.dmg${NC}"
fi

echo -e "${BLUE}Once the release is published, the auto-update feature will detect it for users.${NC}" 