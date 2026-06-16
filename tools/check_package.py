#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import logging
from pathlib import Path

def check_package():
    """检查打包完整性"""
    print("=== 打包完整性检查 ===")
    
    # 检查应用程序目录
    app_dir = Path("dist/HpcManagementSystem.app")
    if not app_dir.exists():
        print(f"错误: 应用程序目录 {app_dir} 不存在")
        return False
    
    print(f"应用程序目录: {app_dir} 存在")
    
    # 检查必要的子目录
    required_subdirs = [
        "Contents/MacOS",
        "Contents/Resources",
        "Contents/Frameworks"
    ]
    
    for subdir in required_subdirs:
        subdir_path = app_dir / subdir
        if not subdir_path.exists():
            print(f"错误: 缺少必要的子目录 {subdir}")
            return False
        print(f"子目录 {subdir} 存在")
    
    # 检查必要的文件
    required_files = [
        "Contents/MacOS/HpcManagementSystem",
        "Contents/Resources/icon.icns",
        "Contents/Resources/resources",
        "Contents/Resources/modules",
        "Contents/Resources/ui"
    ]
    
    for file_path in required_files:
        full_path = app_dir / file_path
        if not full_path.exists():
            print(f"错误: 缺少必要的文件 {file_path}")
            return False
        print(f"文件 {file_path} 存在")
    
    # 检查文件权限
    executable = app_dir / "Contents/MacOS/HpcManagementSystem"
    if not os.access(executable, os.X_OK):
        print(f"错误: 可执行文件 {executable} 没有执行权限")
        return False
    print("可执行文件权限正常")
    
    print("\n打包完整性检查完成，所有必要的文件和目录都存在")
    return True

if __name__ == "__main__":
    check_package() 