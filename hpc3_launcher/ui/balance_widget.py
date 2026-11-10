#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from PyQt5.QtWidgets import QWidget, QHBoxLayout
from PyQt5.QtCore import pyqtSlot

import logging
from modules.balance import BalanceManager
from modules.auth import HPC_SERVER, get_all_existing_users
from core.ssh_session import SSHWorker
from ui.status_view import LiveStatusView
from ui.components import (PageHeader, StatTile, SortItem, BarDelegate, DATA_ROLE,
                           button, divider, make_table, page_layout)

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
        main_layout = page_layout(self)

        self.header = PageHeader(
            "Balance",
            "Service units (SUs) on each account you can charge. Free-tier jobs spend none.")
        self.refresh_btn = button("Refresh", None, self.refresh_balance)
        self.header.add_action(self.refresh_btn)
        main_layout.addWidget(self.header)

        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        self.personal_tile = StatTile("PERSONAL ALLOCATION LEFT", "—", "")
        self.used_tile = StatTile("USED BY YOU", "—", "across all accounts")
        self.accounts_tile = StatTile("ACCOUNTS", "—", "")
        for tile in (self.personal_tile, self.used_tile, self.accounts_tile):
            tiles.addWidget(tile, 1)
        main_layout.addLayout(tiles)

        # Accounts table
        self.accounts_table = make_table(
            ["Account", "Used by you", "Used by the account", "Limit", "Available"], stretch=2)
        self.accounts_table.setItemDelegateForColumn(2, BarDelegate(self))
        for col, width in enumerate((260, 140, 0, 150, 150)):
            if width:
                self.accounts_table.setColumnWidth(col, width)
        main_layout.addWidget(self.accounts_table, 1)

        # Bottom: live status surface (spinner + state + collapsible log)
        main_layout.addWidget(divider())
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
        self.update_ui()
        self.refresh_btn.setEnabled(True)
        self.status_view.finish_ok("balance updated")

    def update_ui(self):
        """Update UI display"""
        if not self.balance_data:
            return
        accounts = self.balance_data.get('accounts') or []
        personal = [a for a in accounts if a['is_personal']]
        if personal:
            left = sum(a['available'] for a in personal)
            limit = sum(a['account_limit'] for a in personal)
            low = limit > 0 and left / limit < 0.1
            self.personal_tile.set(f"{left:,}", f"of {limit:,} SUs",
                                   tone="danger" if low else None)
        else:
            self.personal_tile.set("—", "no personal account found")
        self.used_tile.set(f"{self.balance_data.get('total_usage', 0):,}")
        shared = len(accounts) - len(personal)
        self.accounts_tile.set(str(len(accounts)),
                               f"{len(personal)} personal · {shared} shared")
        self.update_accounts_table()

    def update_accounts_table(self):
        """Update accounts table"""
        table = self.accounts_table
        table.setSortingEnabled(False)
        table.setRowCount(0)
        if not self.balance_data or not self.balance_data['accounts']:
            return

        # Personal accounts first, shared accounts after.
        sorted_accounts = sorted(
            self.balance_data['accounts'],
            key=lambda x: (0 if x['is_personal'] else 1, x['name'])
        )
        table.setRowCount(len(sorted_accounts))
        for row, account in enumerate(sorted_accounts):
            name_item = SortItem(account['name'] + ("  ·  personal" if account['is_personal'] else ""))
            if account['is_personal']:
                font = name_item.font()
                font.setBold(True)
                name_item.setFont(font)
            table.setItem(row, 0, name_item)
            table.setItem(row, 1, SortItem(f"{account['user_usage']:,}",
                                           sort_key=account['user_usage']))
            limit = account['account_limit']
            frac = (account['account_usage'] / limit) if limit > 0 else 0.0
            used = SortItem(f"{account['account_usage']:,}  ({frac:.0%})", sort_key=frac)
            used.setData(DATA_ROLE, frac)
            table.setItem(row, 2, used)
            table.setItem(row, 3, SortItem(f"{limit:,}", sort_key=limit))
            table.setItem(row, 4, SortItem(f"{account['available']:,}",
                                           sort_key=account['available']))
        table.setSortingEnabled(True)

    def show_error(self, error_msg, hint=""):
        """Display an error from either the manager (1 arg) or a worker (msg, hint)."""
        logger.error("%s %s", error_msg, hint)
        self.status_view.finish_error(error_msg, hint)
        self.refresh_btn.setEnabled(True)
