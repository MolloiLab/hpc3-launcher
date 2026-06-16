#!/bin/bash
# 打包脚本 - 创建可分发的HPC管理系统包

# 确保脚本在错误时停止
set -e

echo "===== HPC管理系统打包工具 ====="
echo "此脚本将创建多种格式的可分发包"
echo

# 确保所需工具已安装
check_dependency() {
  if ! command -v $1 &> /dev/null; then
    echo "警告: 未找到 $1，将尝试使用python3"
    return 1
  fi
  return 0
}

check_dependency python3
if [ $? -ne 0 ]; then
  echo "错误: 未找到python3，请先安装"
  exit 1
fi

# 检查图标文件是否存在
if [ ! -f "hpc3_launcher/resources/icon.icns" ]; then
  echo "生成应用程序图标..."
  conda activate hpc_env && python process_icon.py
  if [ $? -ne 0 ]; then
    echo "警告: 图标生成失败，将使用默认图标"
  fi
fi

# 创建临时构建目录
BUILD_DIR="build_tmp"
rm -rf $BUILD_DIR
mkdir -p $BUILD_DIR

echo "1. 安装构建依赖..."
python3 -m pip install --upgrade pip setuptools wheel pyinstaller

echo "2. 创建源代码分发包..."
python3 setup.py sdist bdist_wheel
cp dist/*.tar.gz $BUILD_DIR/ 2>/dev/null || :
cp dist/*.whl $BUILD_DIR/ 2>/dev/null || :

echo "3. 使用PyInstaller创建独立可执行文件..."
python3 pyinstaller_build.py

# 确定操作系统类型
if [[ "$OSTYPE" == "darwin"* ]]; then
  # macOS
  OS_NAME="macOS"
  APP_PATH="dist/HPC管理系统.app"
  
  if [ -d "$APP_PATH" ]; then
    # 创建DMG镜像
    echo "创建DMG镜像..."
    VOLUME_NAME="HPC管理系统"
    DMG_PATH="$BUILD_DIR/HPC管理系统-macOS.dmg"
    
    # 创建临时DMG
    hdiutil create -volname "$VOLUME_NAME" -srcfolder "$APP_PATH" -ov -format UDBZ "$DMG_PATH"
    
    echo "DMG镜像创建完成: $DMG_PATH"
    
    # 同时创建ZIP备份
    echo "创建ZIP备份..."
    zip -r "$BUILD_DIR/HPC管理系统-macOS.zip" "$APP_PATH" dist/docs
  else
    echo "警告: $APP_PATH 不存在，跳过DMG创建"
    # 尝试查找其他可执行文件
    if [ -f "dist/HPC管理系统" ]; then
      echo "找到可执行文件: dist/HPC管理系统"
      zip -r "$BUILD_DIR/HPC管理系统-macOS.zip" "dist/HPC管理系统" dist/docs
    fi
  fi
elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
  # Linux
  OS_NAME="Linux"
  EXECUTABLE="dist/HPC管理系统"
  zip -r "$BUILD_DIR/HPC管理系统-Linux.zip" "$EXECUTABLE" dist/docs
elif [[ "$OSTYPE" == "msys"* ]] || [[ "$OSTYPE" == "win32" ]]; then
  # Windows
  OS_NAME="Windows"
  EXECUTABLE="dist/HPC管理系统.exe"
  # 在Windows上，使用PowerShell进行压缩
  powershell -Command "Compress-Archive -Path '$EXECUTABLE', 'dist/docs' -DestinationPath '$BUILD_DIR/HPC管理系统-Windows.zip'"
else
  echo "不支持的操作系统: $OSTYPE"
  exit 1
fi

echo "4. 复制说明文档..."
cp README.md INSTALL.md $BUILD_DIR/ 2>/dev/null || :
cp environment.yml $BUILD_DIR/ 2>/dev/null || :

# 复制图标到分发包
echo "5. 复制应用图标..."
mkdir -p "$BUILD_DIR/icons"
cp hpc3_launcher/resources/icon.* "$BUILD_DIR/icons/" 2>/dev/null || :

echo "===== 打包完成 ====="
echo "生成的包位于 $BUILD_DIR 目录"
echo "可分发文件:"
ls -la $BUILD_DIR
echo
echo "建议的分发方式:"
if [[ "$OSTYPE" == "darwin"* ]]; then
  echo "1. 对于macOS用户: HPC管理系统-macOS.dmg (应用程序镜像)"
else
  echo "1. 对于大多数用户: HPC管理系统-$OS_NAME.zip (独立可执行文件)"
fi
echo "2. 对于Python用户: hpc_management_system-1.0.0.tar.gz (pip安装包)"
echo "3. 对于Conda用户: environment.yml (conda环境)"
echo 