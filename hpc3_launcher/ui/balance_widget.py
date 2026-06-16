#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
                           QTableWidget, QTableWidgetItem, QHeaderView, QStyle, 
                           QFrame, QSplitter, QGridLayout, QProgressBar)
from PyQt5.QtCore import Qt, pyqtSlot
from PyQt5.QtGui import QFont, QColor, QBrush

import logging
from modules.balance import BalanceManager
from modules.auth import HPC_SERVER, get_all_existing_users
from core.ssh_session import SSHWorker
from ui.status_view import LiveStatusView

# Configure logging
logger = logging.getLogger('BalanceWidget')

class BalanceWidget(QWidget):
    """Account available computing balance widget, displays user's computing resource usage and balance"""
    
    def __init__(self, parent=None, username=None):
        super().__init__(parent)
        
        # User information
        self.username = username
        self.balance_manager = None
        self._worker = None

        # Account data
        self.balance_data = None
        
        # Initialize balance manager
        self.init_balance_manager()
        
        # Initialize UI
        self.init_ui()
        
        # Load data
        if self.balance_manager:
            self.refresh_balance()
    
    def init_balance_manager(self):
        """Initialize balance manager"""
        if not self.username:
            return
        
        # Get SSH key path
        users = get_all_existing_users()
        key_path = None
        
        for user in users:
            if user['username'] == self.username:
                key_path = user['key_path']
                break
        
        if not key_path:
            return
        
        # Create balance manager
        self.balance_manager = BalanceManager(
            hostname=HPC_SERVER,
            username=self.username,
            key_path=key_path
        )
        
        # Connect signals
        self.balance_manager.balance_updated.connect(self.update_balance_data)
        self.balance_manager.error_occurred.connect(self.show_error)
    
    def init_ui(self):
        """Initialize UI components"""
        main_layout = QVBoxLayout(self)
        
        # Top control bar
        control_layout = QHBoxLayout()
        
        # Refresh button with icon
        self.refresh_btn = QPushButton("Refresh Balance Info")
        self.refresh_btn.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        self.refresh_btn.clicked.connect(self.refresh_balance)
        control_layout.addWidget(self.refresh_btn)
        
        # Refresh status indicator
        self.refresh_indicator = QLabel("Ready")
        self.refresh_indicator.setStyleSheet("color: green;")
        control_layout.addWidget(self.refresh_indicator)
        
        control_layout.addStretch()
        
        # Add control bar to main layout
        main_layout.addLayout(control_layout)
        
        # Overview panel
        overview_frame = QFrame()
        overview_frame.setFrameShape(QFrame.StyledPanel)
        overview_layout = QGridLayout(overview_frame)
        
        # Username label
        username_layout = QHBoxLayout()
        username_label = QLabel("Username:")
        username_label.setFont(QFont('Arial', 12, QFont.Bold))
        username_layout.addWidget(username_label)
        
        self.username_value = QLabel(self.username or "Not Logged In")
        self.username_value.setFont(QFont('Arial', 12))
        username_layout.addWidget(self.username_value)
        username_layout.addStretch()
        
        # Total available resources progress bar
        resource_layout = QVBoxLayout()
        resource_label = QLabel("Total Computing Resources:")
        resource_label.setFont(QFont('Arial', 12, QFont.Bold))
        resource_layout.addWidget(resource_label)
        
        self.resource_progress = QProgressBar()
        self.resource_progress.setTextVisible(True)
        self.resource_progress.setFormat("Used: %v SUs / Total: %m SUs (%p%)")
        resource_layout.addWidget(self.resource_progress)
        
        # Add to grid layout
        overview_layout.addLayout(username_layout, 0, 0)
        overview_layout.addLayout(resource_layout, 1, 0)
        
        # Accounts table
        self.accounts_table = QTableWidget()
        self.accounts_table.setColumnCount(5)
        self.accounts_table.setHorizontalHeaderLabels([
            "Account Name", "Personal Usage", "Total Account Usage", "Account Limit", "Available"
        ])
        self.accounts_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.accounts_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.accounts_table.setSortingEnabled(True)
        
        # Create splitter, place overview at the top and details at the bottom
        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(overview_frame)
        splitter.addWidget(self.accounts_table)
        
        # Set default split ratio
        splitter.setSizes([100, 500])
        
        # Add splitter to main layout
        main_layout.addWidget(splitter)
        
        # Bottom: live status surface (spinner + state + collapsible log)
        self.status_view = LiveStatusView(self)
        main_layout.addWidget(self.status_view)

    @pyqtSlot()
    def refresh_balance(self):
        """Refresh balance information off the UI thread."""
        if not self.balance_manager:
            self.show_error("No SSH key for this user — sign in again to create one.")
            return
        if self._worker and self._worker.isRunning():
            return  # a refresh is already in flight

        self.refresh_btn.setEnabled(False)
        self.refresh_indicator.setText("Refreshing…")
        self.refresh_indicator.setStyleSheet("color: orange;")
        self.status_view.start("Loading balance", "querying sbank on HPC3")

        # get_user_balance() emits balance_updated / error_occurred itself, which
        # we've wired to update_balance_data / show_error. The worker exists only to
        # run that (blocking) SSH call off the UI thread and keep the spinner alive.
        self._worker = SSHWorker(
            lambda rep: self.balance_manager.get_user_balance(reporter=rep),
            parent=self, label="balance",
        )
        self._worker.progress.connect(self.status_view.set_state)
        self._worker.log.connect(self.status_view.append_log)
        self._worker.failed.connect(self.show_error)
        self._worker.start()

    @pyqtSlot(dict)
    def update_balance_data(self, balance_data):
        """Update balance data"""
        self.balance_data = balance_data

        # Update UI
        self.update_ui()

        # Restore UI state
        self.refresh_btn.setEnabled(True)
        self.refresh_indicator.setText("Ready")
        self.refresh_indicator.setStyleSheet("color: green;")
        self.status_view.finish_ok("balance updated")
    
    def update_ui(self):
        """Update UI display"""
        if not self.balance_data:
            return
        
        # Update username
        self.username_value.setText(self.balance_data['username'])
        
        # Update total resources progress bar
        total_usage = self.balance_data['total_usage']
        total_available = self.balance_data['total_available']
        total_limit = total_usage + total_available
        
        self.resource_progress.setMaximum(total_limit if total_limit > 0 else 100)
        self.resource_progress.setValue(total_usage)
        
        # Set progress bar color
        usage_ratio = (total_usage / total_limit * 100) if total_limit > 0 else 0
        self.set_progress_bar_color(usage_ratio)
        
        # Update accounts table
        self.update_accounts_table()
    
    def update_accounts_table(self):
        """Update accounts table"""
        # Disable sorting to prevent confusion during reload
        self.accounts_table.setSortingEnabled(False)
        self.accounts_table.setRowCount(0)
        
        if not self.balance_data or not self.balance_data['accounts']:
            return
        
        # Sort by account type: personal accounts first, shared accounts later
        sorted_accounts = sorted(
            self.balance_data['accounts'], 
            key=lambda x: (0 if x['is_personal'] else 1, x['name'])
        )
        
        # Add a row for each account
        for row, account in enumerate(sorted_accounts):
            self.accounts_table.insertRow(row)
            
            # Account name
            name_item = QTableWidgetItem(account['name'])
            # Personal accounts are bold
            if account['is_personal']:
                font = name_item.font()
                font.setBold(True)
                name_item.setFont(font)
                name_item.setForeground(QBrush(QColor(0, 0, 255)))  # Personal accounts in blue
            self.accounts_table.setItem(row, 0, name_item)
            
            # Personal usage
            user_usage_item = QTableWidgetItem(f"{account['user_usage']:,}")
            self.accounts_table.setItem(row, 1, user_usage_item)
            
            # Total account usage
            account_usage_item = QTableWidgetItem(f"{account['account_usage']:,}")
            self.accounts_table.setItem(row, 2, account_usage_item)
            
            # Account limit
            account_limit_item = QTableWidgetItem(f"{account['account_limit']:,}")
            self.accounts_table.setItem(row, 3, account_limit_item)
            
            # Available
            available_item = QTableWidgetItem(f"{account['available']:,}")
            # Set color based on availability
            usage_ratio = (account['account_usage'] / account['account_limit'] * 100) if account['account_limit'] > 0 else 0
            self.set_item_color_by_usage(available_item, usage_ratio)
            self.accounts_table.setItem(row, 4, available_item)
        
        # Restore sorting
        self.accounts_table.setSortingEnabled(True)
    
    def set_progress_bar_color(self, usage_ratio):
        """Set progress bar color based on usage rate"""
        if usage_ratio > 90:
            self.resource_progress.setStyleSheet("""
                QProgressBar::chunk { background-color: red; }
                QProgressBar { text-align: center; }
            """)
        elif usage_ratio > 70:
            self.resource_progress.setStyleSheet("""
                QProgressBar::chunk { background-color: orange; }
                QProgressBar { text-align: center; }
            """)
        else:
            self.resource_progress.setStyleSheet("""
                QProgressBar::chunk { background-color: green; }
                QProgressBar { text-align: center; }
            """)
    
    def set_item_color_by_usage(self, item, usage_ratio):
        """Set table item color based on usage rate"""
        if usage_ratio > 90:
            item.setForeground(QBrush(QColor(255, 0, 0)))  # Red
        elif usage_ratio > 70:
            item.setForeground(QBrush(QColor(255, 165, 0)))  # Orange
        else:
            item.setForeground(QBrush(QColor(0, 128, 0)))  # Green
    
    def show_error(self, error_msg, hint=""):
        """Display an error from either the manager (1 arg) or a worker (msg, hint)."""
        self.refresh_indicator.setText("Error")
        self.refresh_indicator.setStyleSheet("color: red;")
        logger.error("%s %s", error_msg, hint)
        self.status_view.finish_error(error_msg, hint)
        self.refresh_btn.setEnabled(True) 