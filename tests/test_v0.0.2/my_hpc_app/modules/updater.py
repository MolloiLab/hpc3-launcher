#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Updater module for UCI-ClusterManager
Checks for updates and provides functionality to download and apply them
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
VERSION = "0.0.2"  # Current version (updated to 0.0.2)
GITHUB_REPO = "songliangyu/UCI-ClusterManager"  # Update this with your actual GitHub repository
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
UPDATE_CHECK_INTERVAL = 24 * 60 * 60  # Check every 24 hours (in seconds)

class UpdateWorker(QThread):
    """Worker thread for checking and downloading updates"""
    
    # Signals
    update_available = pyqtSignal(dict)  # Emitted when an update is available
    no_update = pyqtSignal()  # Emitted when no update is available
    update_error = pyqtSignal(str)  # Emitted when an error occurs
    update_progress = pyqtSignal(int, str)  # Emitted to report download progress
    update_downloaded = pyqtSignal(str)  # Emitted when update is downloaded
    
    def __init__(self, parent=None, check_only=True):
        """
        Initialize update worker
        
        Args:
            parent: Parent object
            check_only: If True, only check for updates without downloading
        """
        super().__init__(parent)
        self.check_only = check_only
        self.cancel_requested = False
        
    def run(self):
        """Main worker function that runs in a separate thread"""
        try:
            # Check for updates
            update_info = self.check_for_updates()
            
            if not update_info:
                self.no_update.emit()
                return
                
            # If we only need to check, emit signal and return
            if self.check_only:
                self.update_available.emit(update_info)
                return
                
            # If we need to download, proceed with download
            self.update_progress.emit(0, "Starting download...")
            download_path = self.download_update(update_info)
            
            if download_path:
                self.update_downloaded.emit(download_path)
            else:
                self.update_error.emit("Failed to download update")
                
        except Exception as e:
            logger.error(f"Update error: {str(e)}")
            self.update_error.emit(f"Update error: {str(e)}")
    
    def check_for_updates(self):
        """
        Check for updates from GitHub releases
        
        Returns:
            dict: Update information if update available, None otherwise
        """
        try:
            # Make request to GitHub API
            logger.info(f"Checking for updates from {GITHUB_API_URL}")
            self.update_progress.emit(0, "Checking for updates...")
            
            response = requests.get(GITHUB_API_URL, timeout=10)
            response.raise_for_status()  # Raise exception for HTTP errors
            
            release_data = response.json()
            latest_version = release_data.get('tag_name', 'v0.0.0').lstrip('v')
            
            logger.info(f"Current version: {VERSION}, Latest version: {latest_version}")
            
            # Compare versions
            if version.parse(latest_version) > version.parse(VERSION):
                # Find appropriate asset for current platform
                system = platform.system().lower()
                assets = release_data.get('assets', [])
                
                # Find correct asset for platform
                asset = None
                for a in assets:
                    name = a.get('name', '').lower()
                    if system == 'darwin' and ('.pkg' in name or '.dmg' in name):
                        asset = a
                        break
                    elif system == 'windows' and '.exe' in name:
                        asset = a
                        break
                    elif system == 'linux' and ('.deb' in name or '.rpm' in name):
                        asset = a
                        break
                
                if not asset:
                    logger.warning(f"No suitable asset found for {system}")
                    return None
                
                # Construct update info
                update_info = {
                    'version': latest_version,
                    'current_version': VERSION,
                    'download_url': asset.get('browser_download_url'),
                    'release_notes': release_data.get('body', 'No release notes available'),
                    'asset_name': asset.get('name'),
                    'published_at': release_data.get('published_at'),
                    'system': system
                }
                
                logger.info(f"Update available: {latest_version}")
                return update_info
            else:
                logger.info("No updates available")
                return None
                
        except Exception as e:
            logger.error(f"Error checking for updates: {str(e)}")
            raise
    
    def download_update(self, update_info):
        """
        Download update from the provided URL
        
        Args:
            update_info: Dictionary containing update information
            
        Returns:
            str: Path to downloaded file, None if download failed
        """
        try:
            download_url = update_info.get('download_url')
            if not download_url:
                logger.error("No download URL provided")
                return None
                
            asset_name = update_info.get('asset_name')
            download_path = os.path.join(tempfile.gettempdir(), asset_name)
            
            logger.info(f"Downloading update from {download_url} to {download_path}")
            self.update_progress.emit(10, f"Downloading {asset_name}...")
            
            # Stream download to show progress
            response = requests.get(download_url, stream=True, timeout=60)
            response.raise_for_status()
            
            # Get file size if available
            total_size = int(response.headers.get('content-length', 0))
            
            # Download with progress tracking
            downloaded = 0
            with open(download_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if self.cancel_requested:
                        logger.info("Download canceled")
                        return None
                        
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        # Update progress if we know the total size
                        if total_size > 0:
                            progress = int(downloaded / total_size * 90) + 10  # 10-100%
                            self.update_progress.emit(progress, f"Downloading: {progress}%")
            
            self.update_progress.emit(100, "Download complete")
            logger.info(f"Download completed: {download_path}")
            return download_path
            
        except Exception as e:
            logger.error(f"Error downloading update: {str(e)}")
            return None
    
    def cancel(self):
        """Cancel the update process"""
        self.cancel_requested = True

class UpdateManager(QObject):
    """
    Update manager for checking and applying updates
    """
    
    # Signals
    update_available = pyqtSignal(dict)  # Emitted when an update is available
    no_update = pyqtSignal()  # Emitted when no update is available
    update_error = pyqtSignal(str)  # Emitted when an error occurs
    update_progress = pyqtSignal(int, str)  # Emitted to report progress
    update_downloaded = pyqtSignal(str)  # Emitted when update is downloaded
    
    def __init__(self, parent=None):
        """Initialize update manager"""
        super().__init__(parent)
        self.worker = None
        
    def check_for_updates(self, silent=False):
        """
        Check for updates
        
        Args:
            silent: If True, don't emit signals if no update is available
        """
        # Cancel any existing worker
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait()
        
        # Create and start new worker
        self.worker = UpdateWorker(self, check_only=True)
        
        # Connect signals
        if not silent:
            self.worker.no_update.connect(self.no_update)
        self.worker.update_available.connect(self.update_available)
        self.worker.update_error.connect(self.update_error)
        
        # Start worker
        self.worker.start()
    
    def download_update(self, update_info):
        """
        Download an update
        
        Args:
            update_info: Dictionary containing update information
        """
        # Cancel any existing worker
        if self.worker and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait()
        
        # Create and start new worker
        self.worker = UpdateWorker(self, check_only=False)
        
        # Connect signals
        self.worker.update_progress.connect(self.update_progress)
        self.worker.update_downloaded.connect(self.update_downloaded)
        self.worker.update_error.connect(self.update_error)
        
        # Put update info in worker and start
        self.worker._update_info = update_info
        self.worker.start()
    
    def apply_update(self, download_path):
        """
        Apply the downloaded update
        
        Args:
            download_path: Path to the downloaded update file
            
        Returns:
            bool: True if update process started successfully
        """
        if not os.path.exists(download_path):
            logger.error(f"Update file does not exist: {download_path}")
            return False
            
        try:
            system = platform.system().lower()
            
            # macOS: Open .pkg or .dmg
            if system == 'darwin':
                if download_path.endswith('.pkg'):
                    subprocess.Popen(['open', download_path])
                elif download_path.endswith('.dmg'):
                    subprocess.Popen(['open', download_path])
                else:
                    logger.error(f"Unsupported file format for macOS: {download_path}")
                    return False
            
            # Windows: Run .exe
            elif system == 'windows':
                if download_path.endswith('.exe'):
                    subprocess.Popen([download_path])
                else:
                    logger.error(f"Unsupported file format for Windows: {download_path}")
                    return False
            
            # Linux: Open .deb with package manager or run .run
            elif system == 'linux':
                if download_path.endswith('.deb'):
                    subprocess.Popen(['xdg-open', download_path])
                elif download_path.endswith('.rpm'):
                    subprocess.Popen(['xdg-open', download_path])
                else:
                    logger.error(f"Unsupported file format for Linux: {download_path}")
                    return False
            
            logger.info(f"Update process started with {download_path}")
            return True
            
        except Exception as e:
            logger.error(f"Error applying update: {str(e)}")
            return False
    
    def get_current_version(self):
        """Get the current version of the application"""
        return VERSION

# Singleton instance for the application
updater = UpdateManager()

def check_for_updates(silent=False):
    """
    Check for updates (helper function for easier import)
    
    Args:
        silent: If True, don't emit signals if no update is available
    """
    updater.check_for_updates(silent=silent)

def get_current_version():
    """Get the current version of the application"""
    return VERSION 