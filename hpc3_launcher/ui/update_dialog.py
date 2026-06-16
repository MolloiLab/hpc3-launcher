#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Update dialog for HPC3 Launcher.

Shows available updates. Downloading and installing are now two explicit,
user-clicked steps -- the old dialog silently auto-downloaded 500ms after
appearing and then auto-launched the installer (and quit the app) 500ms after
that, with no confirmation, which is startling behaviour for a background check.
"""

import os
import sys
from PyQt5.QtCore import Qt, QSize, QTimer
from PyQt5.QtGui import QIcon, QFont
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QProgressBar, QTextEdit, QMessageBox, QApplication
)

from modules import updater

class UpdateDialog(QDialog):
    """Dialog for showing update information and handling the update process"""
    
    def __init__(self, update_info, parent=None, auto_download=False):
        """
        Initialize update dialog
        
        Args:
            update_info: Dictionary containing update information
            parent: Parent widget
            auto_download: Whether to automatically start download
        """
        super().__init__(parent)
        self.update_info = update_info
        self.download_path = None
        self.auto_download = auto_download
        self.init_ui()
        
        # Start download automatically if requested
        if self.auto_download:
            QTimer.singleShot(500, self.download_update)
        
    def init_ui(self):
        """Initialize the user interface"""
        self.setWindowTitle("Software Update")
        self.setMinimumWidth(500)
        self.setMinimumHeight(400)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        
        # Create layout
        layout = QVBoxLayout()
        
        # Add update information
        title_label = QLabel(f"A new version is available")
        title_label.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(title_label)
        
        # Version information
        version_label = QLabel(f"HPC3 Launcher {self.update_info['version']} is now available - you have {self.update_info['current_version']}")
        layout.addWidget(version_label)
        
        # Release notes
        layout.addWidget(QLabel("What's New:"))
        
        notes_text = QTextEdit()
        notes_text.setReadOnly(True)
        notes_text.setPlainText(self.update_info['release_notes'])
        layout.addWidget(notes_text)
        
        # Progress bar (hidden initially if not auto downloading)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(self.auto_download)
        layout.addWidget(self.progress_bar)
        
        # Status label
        self.status_label = QLabel("Preparing to download...")
        self.status_label.setVisible(self.auto_download)
        layout.addWidget(self.status_label)
        
        # Buttons
        button_layout = QHBoxLayout()
        
        self.remind_button = QPushButton("Later")
        self.remind_button.clicked.connect(self.reject)
        button_layout.addWidget(self.remind_button)
        
        button_layout.addStretch()
        
        self.download_button = QPushButton("Download")
        self.download_button.setDefault(True)
        self.download_button.clicked.connect(self.download_update)
        self.download_button.setVisible(not self.auto_download)  # Hide if auto downloading
        button_layout.addWidget(self.download_button)
        
        self.install_button = QPushButton("Install Now")
        self.install_button.setVisible(False)
        self.install_button.clicked.connect(self.install_update)
        button_layout.addWidget(self.install_button)
        
        layout.addLayout(button_layout)
        
        self.setLayout(layout)
        
        # Connect signals from updater
        updater.updater.update_progress.connect(self.update_progress)
        updater.updater.update_downloaded.connect(self.update_downloaded)
        updater.updater.update_error.connect(self.update_error)
    
    def download_update(self):
        """Start downloading the update"""
        self.progress_bar.setVisible(True)
        self.status_label.setVisible(True)
        self.status_label.setText("Downloading update...")
        self.remind_button.setEnabled(False)
        if hasattr(self, 'download_button'):
            self.download_button.setVisible(False)
        
        # Start download
        updater.updater.download_update(self.update_info)
    
    def update_progress(self, progress, message):
        """
        Update progress bar and status message
        
        Args:
            progress: Progress value (0-100)
            message: Status message
        """
        self.progress_bar.setValue(progress)
        self.status_label.setText(message)
    
    def update_downloaded(self, download_path):
        """
        Handle downloaded update
        
        Args:
            download_path: Path to the downloaded update file
        """
        self.download_path = download_path
        self.status_label.setText(f"Download complete: {os.path.basename(download_path)}")
        
        # Auto install or show install button
        if self.auto_download:
            QTimer.singleShot(500, self.install_update)
        else:
            self.install_button.setVisible(True)
            self.remind_button.setEnabled(True)
            self.remind_button.setText("Install Later")
    
    def update_error(self, error_message):
        """
        Handle update error
        
        Args:
            error_message: Error message
        """
        self.status_label.setText(f"Error: {error_message}")
        self.progress_bar.setVisible(False)
        self.remind_button.setEnabled(True)
        if hasattr(self, 'download_button'):
            self.download_button.setVisible(True)
        
        # Show error message box
        QMessageBox.critical(self, "Update Error", 
                            f"An error occurred during the update process:\n{error_message}")
    
    def install_update(self):
        """Install the downloaded update"""
        if not self.download_path:
            self.update_error("No download path available")
            return
            
        # Apply update
        success = updater.updater.apply_update(self.download_path)
        
        if success:
            # Show success message and close application
            QMessageBox.information(self, "Update Started", 
                                  "The installer has been opened. Follow the installation instructions to update the application.")
            self.accept()
            # Exit application - comment this out for testing
            QApplication.instance().quit()
        else:
            self.update_error("Failed to start update installation")

def check_for_updates_with_ui(parent=None, silent=False, auto_download=False):
    """
    Check for updates and show dialog if update is available
    
    Args:
        parent: Parent widget for the dialog
        silent: If True, only show dialog if update is available
        auto_download: If True, automatically download and install update
        
    Returns:
        bool: True if update is available, False otherwise
    """
    # Define callback handlers
    def on_update_available(update_info):
        dialog = UpdateDialog(update_info, parent, auto_download=auto_download)
        dialog.exec_()
    
    def on_no_update():
        if not silent:
            QMessageBox.information(parent, "No Updates", 
                                   "You are using the latest version of the application.")
    
    def on_error(error_message):
        if not silent:
            QMessageBox.warning(parent, "Update Check Failed", 
                               f"Failed to check for updates:\n{error_message}")
    
    # Connect signals
    updater.updater.update_available.connect(on_update_available)
    updater.updater.no_update.connect(on_no_update)
    updater.updater.update_error.connect(on_error)
    
    # Check for updates
    updater.updater.check_for_updates(silent=silent)
    
    return True

# Test function
if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # Mock update info for testing
    mock_update = {
        'version': '0.1.0',
        'current_version': '0.0.1',
        'download_url': 'https://example.com/download/app.dmg',
        'release_notes': 'This is a test release with some new features:\n\n- Feature 1\n- Feature 2\n- Bug fixes',
        'asset_name': 'app.dmg',
        'published_at': '2023-06-15T12:00:00Z',
        'system': 'darwin'
    }
    
    dialog = UpdateDialog(mock_update, auto_download=True)
    dialog.exec_()
    
    sys.exit(app.exec_()) 