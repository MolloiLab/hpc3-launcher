#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HPC3 Launcher — entry point and main window."""

import sys
import os
import logging

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QAction, QWidget, QVBoxLayout, QMessageBox,
    QDialog, QHBoxLayout, QListWidget, QStackedWidget, QSplitter,
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QIcon, QFont

from ui.login_dialog import LoginDialog
from ui.task_manager_widget import TaskManagerWidget
from ui.node_status_widget import NodeStatusWidget
from ui.balance_widget import BalanceWidget
from ui.vscode_widget import VSCodeWidget
from ui.update_dialog import check_for_updates_with_ui
from modules.updater import get_current_version

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
logger = logging.getLogger(__name__)

APP_NAME = "HPC3 Launcher"


def resource_path(name):
    """Locate a bundled resource in dev and in a PyInstaller --windowed build."""
    candidates = []
    if hasattr(sys, "_MEIPASS"):
        candidates.append(os.path.join(sys._MEIPASS, "resources", name))
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(here, "resources", name))
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def app_icon():
    for name in ("icon.png", "icon.icns", "icon.ico"):
        path = resource_path(name)
        if path:
            return QIcon(path)
    return QIcon()


class MainWindow(QMainWindow):
    PAGES = ["Job Management", "Node Status", "VSCode", "Account Balance"]

    def __init__(self, username=None, node_info=None):
        super().__init__()
        self.username = username
        self.node_info = node_info
        self._init_ui()

    def _init_ui(self):
        self.setWindowTitle(f"{APP_NAME} — v{get_current_version()}")
        self.setGeometry(100, 100, 1200, 800)
        self.setWindowIcon(app_icon())

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)

        self.sidebar = QListWidget()
        self.sidebar.setMinimumWidth(180)
        self.sidebar.setMaximumWidth(280)
        self.sidebar.setFont(QFont("Arial", 13))
        for item in self.PAGES:
            self.sidebar.addItem(item)
        self.sidebar.currentRowChanged.connect(self._display_page)

        self.pages = QStackedWidget()
        self.pages.addWidget(TaskManagerWidget(username=self.username))
        self.pages.addWidget(NodeStatusWidget(username=self.username))
        self.pages.addWidget(self._wrap(VSCodeWidget(username=self.username)))
        self.pages.addWidget(self._wrap(BalanceWidget(username=self.username)))

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.sidebar)
        splitter.addWidget(self.pages)
        splitter.setSizes([200, 1000])
        main_layout.addWidget(splitter)

        self.statusBar().showMessage(f"Signed in as {self.username or 'unknown user'}")
        self._create_menu_bar()
        self.sidebar.setCurrentRow(0)

        # A quiet update check shortly after startup (non-blocking, no auto-install).
        # Skipped under the headless CI self-test so it never reaches the network.
        if not os.environ.get("HPC3_SMOKE_TEST"):
            QTimer.singleShot(3000, lambda: check_for_updates_with_ui(self, silent=True))

    @staticmethod
    def _wrap(widget):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(widget)
        return page

    def _create_menu_bar(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")
        exit_action = QAction("Exit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        help_menu = menubar.addMenu("Help")
        update_action = QAction("Check for Updates", self)
        update_action.triggered.connect(lambda: check_for_updates_with_ui(self, silent=False))
        help_menu.addAction(update_action)
        about_action = QAction("About", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _show_about(self):
        QMessageBox.about(
            self, f"About {APP_NAME}",
            f"<h3>{APP_NAME}</h3>"
            f"<p>Version {get_current_version()}</p>"
            f"<p>A desktop launcher for UCI's HPC3 cluster: jobs, nodes, VSCode "
            f"remote sessions, and SU balance.</p>"
            f"<p>Originally created as UCI-ClusterManager by Song Liangyu and "
            f"contributors; adapted for the Molloi Lab.</p>"
            f"<p>Licensed under the GNU GPL v3.0.</p>"
        )

    def _display_page(self, index):
        self.pages.setCurrentIndex(index)

    def closeEvent(self, event):
        if QMessageBox.question(
            self, "Quit HPC3 Launcher", "Quit HPC3 Launcher?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) == QMessageBox.Yes:
            event.accept()
        else:
            event.ignore()


def _run_smoke_test(app):
    """Headless self-test used by CI to prove the packaged app really launches.

    Builds the main window, confirms the Qt event loop actually starts, writes a
    marker file (named by HPC3_SMOKE_MARKER) and quits. No login or SSH is
    involved: the fake username has no saved key, so every key-gated background
    load short-circuits and nothing touches the network. The marker file -- not
    the process exit code -- is the pass/fail signal, so any background worker
    teardown noise at interpreter shutdown can't produce a false failure.

    Enabled by HPC3_SMOKE_TEST=1; pair with QT_QPA_PLATFORM=offscreen on a
    headless runner. This proves: bundled imports resolve, the Qt platform
    plugin loads, QApplication + the full main window construct, and the event
    loop runs -- exactly the things that silently fail in a broken build.
    """
    window = MainWindow(username="smoketest", node_info=None)
    window.show()

    def _confirm():
        message = ("SMOKE OK: imports loaded, Qt platform plugin up, "
                   "main window constructed, event loop running")
        marker = os.environ.get("HPC3_SMOKE_MARKER")
        if marker:
            try:
                with open(marker, "w", encoding="utf-8") as handle:
                    handle.write(message + "\n")
            except OSError as exc:
                print(f"SMOKE WARN: could not write marker {marker}: {exc}", flush=True)
        print(message, flush=True)
        app.quit()

    QTimer.singleShot(1500, _confirm)
    return app.exec_()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(app_icon())

    if os.environ.get("HPC3_SMOKE_TEST"):
        return _run_smoke_test(app)

    login = LoginDialog()
    if login.exec_() != QDialog.Accepted:
        return 0

    window = MainWindow(username=login.uc_id_input.text().strip(),
                        node_info=login.node_info)
    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
