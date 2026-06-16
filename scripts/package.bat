@echo off
REM 打包脚本 - 创建可分发的HPC管理系统包 (Windows版本)

echo ===== HPC管理系统打包工具 =====
echo 此脚本将创建多种格式的可分发包
echo.

REM 检查Python是否已安装
where python >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo 错误: 未找到Python，请先安装
    exit /b 1
)

REM 创建临时构建目录
set BUILD_DIR=build_tmp
if exist %BUILD_DIR% rmdir /s /q %BUILD_DIR%
mkdir %BUILD_DIR%

echo 1. 安装构建依赖...
python -m pip install --upgrade pip setuptools wheel pyinstaller

echo 2. 创建源代码分发包...
python setup.py sdist bdist_wheel
copy dist\*.tar.gz %BUILD_DIR%\
copy dist\*.whl %BUILD_DIR%\

echo 3. 使用PyInstaller创建独立可执行文件...
python pyinstaller_build.py

REM 创建ZIP文件
echo 4. 创建ZIP包...
powershell -Command "Compress-Archive -Path 'dist\HPC管理系统.exe', 'dist\docs' -DestinationPath '%BUILD_DIR%\HPC管理系统-Windows.zip'"

echo 5. 复制说明文档...
copy README.md %BUILD_DIR%\
copy INSTALL.md %BUILD_DIR%\
copy environment.yml %BUILD_DIR%\

echo ===== 打包完成 =====
echo 生成的包位于 %BUILD_DIR% 目录
echo 可分发文件:
dir %BUILD_DIR%
echo.
echo 建议的分发方式:
echo 1. 对于大多数用户: HPC管理系统-Windows.zip (独立可执行文件)
echo 2. 对于Python用户: hpc_management_system-1.0.0.tar.gz (pip安装包)
echo 3. 对于Conda用户: environment.yml (conda环境)
echo.

pause 