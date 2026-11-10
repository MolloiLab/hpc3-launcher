#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HPC3 Launcher — entry point and main window."""

import sys
import os
import logging

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QAction, QActionGroup, QWidget, QVBoxLayout, QMessageBox,
    QDialog, QHBoxLayout, QListWidget, QStackedWidget, QLabel,
)
from PyQt5.QtCore import Qt, QSize, QTimer, QUrl
from PyQt5.QtGui import QIcon, QDesktopServices

from ui import theme
from ui.components import SegmentedControl
from ui.login_dialog import LoginDialog
from ui.task_manager_widget import TaskManagerWidget
from ui.node_status_widget import NodeStatusWidget
from ui.balance_widget import BalanceWidget
from ui.vscode_widget import VSCodeWidget
from core.ssh_session import HPCSession, HPC_SERVER
from modules.auth import get_all_existing_users
from modules.cluster_view import ClusterInsights
from modules.node_flags import NodeFlagStore
from modules.updater import GITHUB_REPO, get_current_version

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
    # Sessions first: launching and reconnecting to a VSCode session is what the
    # app is opened for nearly every time.
    PAGES = ["Sessions", "Jobs", "GPUs & nodes", "Balance"]

    def __init__(self, username=None, node_info=None):
        super().__init__()
        self.username = username
        self.node_info = node_info
        self.insights = self._make_insights()
        # Nodes the user has flagged as bad: one list, shared by the pages.
        self.flags = NodeFlagStore(parent=self)
        self._init_ui()

    def _make_insights(self):
        """One shared, read-only source of the cluster picture (idle GPUs, queue,
        priority) for the pages that show it. None when there is no saved key."""
        for user in get_all_existing_users():
            if user["username"] == self.username:
                return ClusterInsights(HPCSession(self.username, user["key_path"]),
                                       self.username)
        return None

    def _init_ui(self):
        self.setWindowTitle(f"{APP_NAME} — v{get_current_version()}")
        self.resize(1280, 840)
        self.setMinimumSize(1040, 680)
        self.setWindowIcon(app_icon())

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        main_layout.addWidget(self._build_sidebar())

        self.pages = QStackedWidget()
        self.vscode_page = VSCodeWidget(username=self.username, insights=self.insights,
                                        flags=self.flags)
        self.jobs_page = TaskManagerWidget(username=self.username)
        self.nodes_page = NodeStatusWidget(username=self.username, insights=self.insights,
                                           flags=self.flags)
        self.balance_page = BalanceWidget(username=self.username)
        for page in (self.vscode_page, self.jobs_page, self.nodes_page, self.balance_page):
            self.pages.addWidget(page)
        main_layout.addWidget(self.pages, 1)

        self._create_menu_bar()
        self.sidebar.setCurrentRow(0)

    def _build_sidebar(self):
        side = QWidget()
        side.setObjectName("sidebar")
        side.setFixedWidth(224)
        col = QVBoxLayout(side)
        col.setContentsMargins(0, 18, 0, 16)
        col.setSpacing(4)

        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(20, 0, 16, 12)
        brand_row.setSpacing(10)
        logo = QLabel()
        icon = app_icon()
        if not icon.isNull():
            logo.setPixmap(icon.pixmap(26, 26))
        brand_row.addWidget(logo)
        brand = QLabel(APP_NAME)
        brand.setObjectName("brand")
        brand_row.addWidget(brand, 1)
        col.addLayout(brand_row)

        self.sidebar = QListWidget()
        self.sidebar.setObjectName("nav")
        self.sidebar.setFocusPolicy(Qt.NoFocus)
        self.sidebar.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.sidebar.setIconSize(QSize(18, 18))
        for item in self.PAGES:
            self.sidebar.addItem(item)
        self._paint_nav_icons()
        theme.notifier.changed.connect(self._paint_nav_icons)   # icons are drawn in theme colours
        self.sidebar.currentRowChanged.connect(self._display_page)
        col.addWidget(self.sidebar, 1)

        foot = QVBoxLayout()
        foot.setContentsMargins(16, 0, 16, 0)
        foot.setSpacing(2)
        # Light / dark, where it can be seen (also under View > Appearance).
        self.theme_switch = SegmentedControl([("Auto", "system"), ("Light", "light"),
                                              ("Dark", "dark")])
        self.theme_switch.setToolTips(["Follow the operating system", "Light", "Dark"])
        self.theme_switch.setCurrentData(theme.current_mode())
        self.theme_switch.changed.connect(self._set_theme)
        foot.addWidget(self.theme_switch)
        foot.addSpacing(12)
        user = QLabel(self.username or "not signed in")
        user.setObjectName("user")
        foot.addWidget(user)
        foot.addWidget(QLabel(f"{HPC_SERVER} · v{get_current_version()}"))
        col.addLayout(foot)
        return side

    NAV_ICONS = ("sessions", "jobs", "nodes", "balance")

    def _paint_nav_icons(self):
        for row, name in enumerate(self.NAV_ICONS):
            self.sidebar.item(row).setIcon(theme.nav_icon(name))

    def _set_theme(self, mode):
        """Switch between following the OS, light and dark; remembered next launch."""
        theme.set_mode(mode)
        self.theme_switch.setCurrentData(mode)
        for action in self._theme_actions.actions():
            action.setChecked(action.data() == mode)

    def _create_menu_bar(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")
        exit_action = QAction("Exit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        view_menu = menubar.addMenu("View")
        for index, name in enumerate(self.PAGES):
            action = QAction(name, self)
            action.setShortcut(f"Ctrl+{index + 1}")
            action.triggered.connect(lambda _checked, i=index: self.sidebar.setCurrentRow(i))
            view_menu.addAction(action)
        view_menu.addSeparator()
        appearance = view_menu.addMenu("Appearance")
        self._theme_actions = QActionGroup(self)
        for text, mode in (("Match system", "system"), ("Light", "light"), ("Dark", "dark")):
            action = QAction(text, self, checkable=True)
            action.setData(mode)
            action.setChecked(mode == theme.current_mode())
            action.triggered.connect(lambda _checked, m=mode: self._set_theme(m))
            self._theme_actions.addAction(action)
            appearance.addAction(action)

        help_menu = menubar.addMenu("Help")
        # No in-app updater: new versions are downloaded from the Releases page.
        releases_action = QAction("Download Latest Version…", self)
        releases_action.triggered.connect(lambda: QDesktopServices.openUrl(
            QUrl(f"https://github.com/{GITHUB_REPO}/releases/latest")))
        help_menu.addAction(releases_action)
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
        page = self.pages.currentWidget()
        if hasattr(page, "on_shown"):
            page.on_shown()

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
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    # Windows display scaling is often 125% or 150%. Qt 5 rounds those to 1x or 2x
    # by default, which draws the whole UI a size too small or too large; pass the
    # real factor through instead.
    if hasattr(Qt, "HighDpiScaleFactorRoundingPolicy"):
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(app_icon())
    theme.apply_theme(app)

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
