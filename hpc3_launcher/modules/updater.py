#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""The app's version. release-please rewrites the VERSION line on every release."""

VERSION = "0.6.0"  # x-release-please-version
GITHUB_REPO = "MolloiLab/hpc3-launcher"  # where releases are published


def get_current_version():
    """Get the current version of the application"""
    return VERSION
