#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
import platform
import logging
from PyQt5.QtWidgets import QApplication
import pexpect
import paramiko
import json

def check_environment():
    """检查运行环境"""
    print("=== 环境检查报告 ===")
    
    # 检查Python版本
    print(f"\nPython版本: {sys.version}")
    
    # 检查操作系统
    print(f"操作系统: {platform.system()} {platform.release()}")
    print(f"系统架构: {platform.machine()}")
    
    # 检查PyQt5
    try:
        app = QApplication(sys.argv)
        print("PyQt5: 已安装")
    except Exception as e:
        print(f"PyQt5: 错误 - {str(e)}")
    
    # 检查pexpect
    try:
        pexpect.__version__
        print("pexpect: 已安装")
    except Exception as e:
        print(f"pexpect: 错误 - {str(e)}")
    
    # 检查paramiko
    try:
        paramiko.__version__
        print("paramiko: 已安装")
    except Exception as e:
        print(f"paramiko: 错误 - {str(e)}")
    
    # 检查必要的目录
    required_dirs = ['resources', 'modules', 'ui']
    print("\n检查必要目录:")
    for dir_name in required_dirs:
        if os.path.exists(dir_name):
            print(f"{dir_name}: 存在")
        else:
            print(f"{dir_name}: 不存在")
    
    # 检查环境变量
    print("\n检查环境变量:")
    env_vars = ['PATH', 'PYTHONPATH']
    for var in env_vars:
        value = os.environ.get(var, '未设置')
        print(f"{var}: {value}")

if __name__ == "__main__":
    check_environment() 