#!/bin/bash
# HPC3 Launcher 构建脚本
# 在conda环境中构建应用并创建DMG安装包

set -e  # 出错时停止

# 彩色输出
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # 无颜色

echo -e "${BLUE}HPC3 Launcher 构建脚本${NC}"
echo -e "${BLUE}=============================${NC}"

# 激活conda环境
echo -e "${BLUE}激活conda环境hpc_env...${NC}"
source $(conda info --base)/etc/profile.d/conda.sh
conda activate hpc_env
echo -e "${BLUE}Python路径: $(which python)${NC}"
echo -e "${BLUE}Python版本: $(python --version)${NC}"

# 获取版本号
VERSION=$(grep "VERSION = " "hpc3_launcher/modules/updater.py" | cut -d'"' -f2)
if [ -z "$VERSION" ]; then
    VERSION="0.0.1"  # 默认版本
    echo -e "${YELLOW}警告: 无法从updater.py获取版本号，使用默认版本${VERSION}${NC}"
fi
echo -e "${BLUE}构建版本: ${VERSION}${NC}"

# 清理旧的构建文件
echo -e "${BLUE}清理旧的构建文件...${NC}"
rm -rf build dist *.dmg *.spec

# 运行PyInstaller构建
echo -e "${BLUE}使用PyInstaller构建应用...${NC}"
cd scripts && bash direct_build.sh && cd ..

# 检查构建结果
if [ ! -d "dist/HPC3-Launcher.app" ]; then
    echo -e "${RED}构建失败: 应用程序不存在${NC}"
    exit 1
fi

# 创建DMG
echo -e "${BLUE}创建DMG安装包...${NC}"
python create_dmg.py

# 检查DMG
DMG_NAME="HPC3-Launcher-${VERSION}-macos.dmg"
if [ ! -f "$DMG_NAME" ]; then
    echo -e "${RED}DMG创建失败${NC}"
    exit 1
fi

echo -e "${GREEN}构建成功!${NC}"
echo -e "${GREEN}应用程序: dist/HPC3-Launcher.app${NC}"
echo -e "${GREEN}安装包: ${DMG_NAME}${NC}" 