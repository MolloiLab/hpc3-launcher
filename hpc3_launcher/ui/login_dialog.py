#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Login dialog.

Rewritten so login never blocks the UI thread. Both paths -- "log in with a saved
key" and "create a new key (password + DUO)" -- run inside an SSHWorker and stream
their progress into a LiveStatusView, exactly like PlutoSpace narrates a remote
session. Gone:
  * the synchronous key-verify-against-the-cluster that froze the window
  * the fake QProgressDialog that counted to 100 over 20s waiting for nothing
  * the silent Duo Push (the status line now says "Approve the Duo Push…")
"""

import logging

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QDialog, QLineEdit, QPushButton, QFormLayout, QMessageBox, QVBoxLayout,
    QHBoxLayout, QListWidget, QListWidgetItem, QLabel, QGroupBox, QGridLayout,
)

from core.ssh_session import SSHWorker, SSHError, probe_host, HPC_SERVER
from modules.auth import (
    get_all_existing_users, delete_user_key, check_and_login_with_key,
    get_node_info_via_key,
)
from modules.ssh_key_uploader import generate_and_upload_ssh_key
from ui.status_view import LiveStatusView

logger = logging.getLogger(__name__)

# Last successful node info, exposed for `from ui.login_dialog import get_last_node_info`.
LAST_NODE_INFO = None


def get_last_node_info():
    return LAST_NODE_INFO


class LoginDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sign in to HPC3")
        self.setMinimumWidth(520)
        self.selected_user = None
        self.node_info = None          # read by main() after a successful exec
        self._worker = None

        self.users = get_all_existing_users()
        self._build_ui()

        if self.users and self.user_list.count() > 0:
            self.user_list.setCurrentRow(0)
            self.on_user_selected(self.user_list.item(0))

    # --- UI --------------------------------------------------------------------

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # Saved users
        saved_group = QGroupBox("Saved accounts")
        saved_layout = QGridLayout(saved_group)
        self.user_list = QListWidget()
        self.user_list.setMinimumHeight(110)
        self._populate_user_list()
        self.user_list.itemClicked.connect(self.on_user_selected)
        self.user_list.itemDoubleClicked.connect(lambda _i: self.login_with_key())
        saved_layout.addWidget(self.user_list, 0, 0, 1, 2)

        self.key_login_button = QPushButton("Sign in with saved key")
        self.key_login_button.clicked.connect(self.login_with_key)
        self.key_login_button.setEnabled(False)
        self.delete_button = QPushButton("Forget account")
        self.delete_button.clicked.connect(self.delete_selected_user)
        saved_layout.addWidget(self.key_login_button, 1, 0)
        saved_layout.addWidget(self.delete_button, 1, 1)
        layout.addWidget(saved_group)

        # New account
        new_group = QGroupBox("New account (first time on this computer)")
        form = QFormLayout(new_group)
        self.uc_id_input = QLineEdit()
        self.uc_id_input.setPlaceholderText("your UCInetID")
        form.addRow("UCInetID:", self.uc_id_input)
        self.password_input = QLineEdit()
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.returnPressed.connect(self.handle_new_user_login)
        form.addRow("Password:", self.password_input)
        self.new_user_button = QPushButton("Sign in and remember me")
        self.new_user_button.setMinimumHeight(36)
        self.new_user_button.clicked.connect(self.handle_new_user_login)
        form.addRow(self.new_user_button)
        hint = QLabel("You'll get a Duo Push — approve it on your phone. "
                      "Your password is used once to install a key and is never stored.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888; font-size: 11px;")
        form.addRow(hint)
        layout.addWidget(new_group)

        # Live status surface (shared by both flows)
        self.status_view = LiveStatusView(self)
        layout.addWidget(self.status_view)

    def _populate_user_list(self):
        self.user_list.clear()
        if not self.users:
            placeholder = QListWidgetItem("No saved accounts yet")
            placeholder.setFlags(Qt.NoItemFlags)
            self.user_list.addItem(placeholder)
            return
        for user in self.users:
            item = QListWidgetItem(user["username"])
            item.setData(Qt.UserRole, user)
            self.user_list.addItem(item)

    def on_user_selected(self, item):
        user = item.data(Qt.UserRole)
        if not user:
            return
        self.selected_user = user
        self.uc_id_input.setText(user["username"])
        self.key_login_button.setEnabled(True)

    def delete_selected_user(self):
        if not self.selected_user:
            QMessageBox.warning(self, "Pick an account", "Select a saved account first.")
            return
        username = self.selected_user["username"]
        if QMessageBox.question(
            self, "Forget account",
            f"Forget {username}? This removes its local key; you'll re-enter your "
            f"password and Duo next time.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        if delete_user_key(username):
            self.users = get_all_existing_users()
            self._populate_user_list()
            self.selected_user = None
            self.uc_id_input.clear()
            self.key_login_button.setEnabled(False)
        else:
            QMessageBox.critical(self, "Error", f"Could not forget {username}.")

    # --- worker plumbing -------------------------------------------------------

    def _set_busy(self, busy):
        for w in (self.key_login_button, self.delete_button, self.new_user_button,
                  self.user_list, self.uc_id_input, self.password_input):
            w.setEnabled(not busy)
        # The key button is only meaningful with a selection.
        if not busy:
            self.key_login_button.setEnabled(self.selected_user is not None)

    def _run(self, task, start_label, start_detail=""):
        self._set_busy(True)
        self.status_view.start(start_label, start_detail)
        self._worker = SSHWorker(task, parent=self, label=start_label)
        self._worker.progress.connect(self.status_view.set_state)
        self._worker.log.connect(self.status_view.append_log)
        self._worker.failed.connect(self._on_failed)
        self._worker.succeeded.connect(self._on_login_ok)
        self._worker.start()

    def _on_failed(self, message, hint):
        self.status_view.finish_error(message, hint)
        self._set_busy(False)

    def _on_login_ok(self, result):
        global LAST_NODE_INFO
        username, node_info = result
        self.node_info = node_info
        LAST_NODE_INFO = node_info
        self.uc_id_input.setText(username)
        self.password_input.clear()
        self.status_view.finish_ok("Connected — opening HPC3 Launcher")
        # Let the green check land for a beat, then enter the app.
        QTimer.singleShot(450, self.accept)

    # --- the two login paths ---------------------------------------------------

    def login_with_key(self):
        if not self.selected_user:
            QMessageBox.warning(self, "Pick an account", "Select a saved account first.")
            return
        username = self.selected_user["username"]

        def task(rep):
            ok, _name, err = check_and_login_with_key(username, rep)
            if not ok:
                raise SSHError(err or "saved key didn't work",
                               "Use the New account box to re-enter your password.")
            node_info = get_node_info_via_key(username, rep)
            return username, node_info

        self._run(task, f"Signing in as {username}", f"using your saved key on {HPC_SERVER}")

    def handle_new_user_login(self):
        uc_id = self.uc_id_input.text().strip()
        password = self.password_input.text()
        if not uc_id or not password:
            QMessageBox.warning(self, "Missing info", "Enter your UCInetID and password.")
            return

        def task(rep):
            rep.state("checking", f"checking {HPC_SERVER} is reachable")
            ok, detail = probe_host()
            if not ok:
                raise SSHError(f"can't reach {HPC_SERVER}",
                               detail + " — connect to the campus VPN if you're off-campus.")
            generate_and_upload_ssh_key(uc_id, password, reporter=rep, force=True)
            node_info = get_node_info_via_key(uc_id, rep)
            return uc_id, node_info

        self._run(task, f"Setting up {uc_id}", "creating and uploading your key")
