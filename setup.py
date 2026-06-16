#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from setuptools import setup, find_packages

setup(
    name="hpc_management_system",
    version="1.0.0",
    description="HPC集群管理桌面应用程序",
    author="HPC Team",
    author_email="example@example.com",
    packages=find_packages(),
    install_requires=[
        "PyQt5>=5.15.0",
        "paramiko>=3.5.0",
        "pexpect>=4.9.0",
        "bcrypt>=4.3.0",
        "cryptography>=44.0.0",
    ],
    entry_points={
        'console_scripts': [
            'hpc_management=hpc3_launcher.main:main',
        ],
    },
    include_package_data=True,
    package_data={
        'hpc3_launcher': ['resources/*'],
    },
    python_requires='>=3.8',
) 