#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One place for how the app looks.

Every colour, radius and font size the UI uses comes from here, so a page never
hard-codes ``color: green`` again (the old pages did, and were unreadable in dark
mode). ``apply_theme(app)`` installs the Fusion style, a palette and one
stylesheet; widgets opt into variants with dynamic properties::

    button.setProperty("variant", "primary")     # primary / danger / ghost
    label.setProperty("role", "title")           # title / subtitle / muted / section
    frame.setProperty("card", True)

The look follows T3 Code's, and what makes that look is structure more than
colour: content sits directly on the canvas, there is at most one soft floating
panel per column instead of a box round everything, lists are plain rows with no
table chrome, controls are quiet fills rather than outlines, the sidebar is a
tinted surface with a tinted selection, and a state is a small coloured dot
beside plain text.

Light or dark is the user's choice -- ``set_mode("system" | "light" | "dark")``,
remembered in the app's settings -- and can change while the app is running, so
nothing may cache a colour: paint with ``color()`` at paint time, or style through
a dynamic property. ``notifier.changed`` fires after a switch for the few places
that must redraw by hand. ``HPC3_THEME=light|dark`` overrides everything.

``apply_theme`` also installs one application-wide event filter that gives every
clickable control a pointing-hand cursor, and (on Windows) matches each window's
title bar to the theme.
"""

import os
import subprocess
import sys
import tempfile

from PyQt5.QtCore import Qt, QEvent, QObject, QPointF, QSettings, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontDatabase, QPalette, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import (
    QApplication, QAbstractButton, QAbstractItemView, QComboBox, QHeaderView, QTabBar,
    QWidget,
)

MODES = ("system", "light", "dark")
SETTINGS_KEY = "ui/theme"

LIGHT = {
    "name": "light",
    # canvas / floating panel / control fill, lightest to the eye last
    "bg": "#F6F7F9", "surface": "#EFF1F4", "surface2": "#FFFFFF",
    "border": "#E1E5EA", "border_strong": "#CED4DC",
    "text": "#1B1C1F", "muted": "#6A7079", "faint": "#9AA1AB",
    "accent": "#2F5FD0", "accent_hover": "#264FB3", "accent_soft": "#D5E0EE",
    "on_accent": "#FFFFFF",
    "ok": "#0E9F6E", "ok_soft": "#E3F5EE",
    "warn": "#C27803", "warn_soft": "#FBF1DC",
    "danger": "#DC3F45", "danger_soft": "#FBE6E7",
    "info": "#2F6FD0", "info_soft": "#E2ECF9",
    # The sidebar is a tinted surface with no line beside it.
    "sidebar": "#ECEEF2", "sidebar_text": "#5F656E", "sidebar_active": "#1B1C1F",
    "sidebar_hover": "#E2E5EA", "sidebar_selected": "#D2DDEA",
    # Who holds a GPU. Paid and free are deliberately far apart in hue.
    "gpu_paid": "#9AA1AB", "gpu_free": "#3F74DA", "gpu_idle": "#10B981",
    "track": "#E4E7EC",
}

DARK = {
    "name": "dark",
    "bg": "#0B0B0C", "surface": "#141416", "surface2": "#1E1E21",
    "border": "#222226", "border_strong": "#303036",
    "text": "#F2F2F3", "muted": "#A0A0A8", "faint": "#6C6C75",
    "accent": "#3E66E0", "accent_hover": "#4C74EE", "accent_soft": "#1A2233",
    "on_accent": "#FFFFFF",
    "ok": "#34D399", "ok_soft": "#0C2A20",
    "warn": "#FBBF24", "warn_soft": "#2E230A",
    "danger": "#F87171", "danger_soft": "#2D1414",
    "info": "#60A5FA", "info_soft": "#101F38",
    "sidebar": "#0B0B0C", "sidebar_text": "#A0A0A8", "sidebar_active": "#F2F2F3",
    "sidebar_hover": "#17171A", "sidebar_selected": "#1A2233",
    "gpu_paid": "#6E6E78", "gpu_free": "#5B8DEF", "gpu_idle": "#34D399",
    "track": "#232327",
}

_current = dict(LIGHT)
_asset_dir = None
_filter = None
_startup_dark = None        # what the OS looked like before we replaced the palette


class _Notifier(QObject):
    changed = pyqtSignal()


notifier = _Notifier()


def colors():
    """The active palette (a dict of hex strings)."""
    return _current


def color(key):
    return QColor(_current[key])


def tone_colors(tone):
    """(foreground, background) hex pair for a pill of the given tone."""
    c = _current
    return {
        "ok": (c["ok"], c["ok_soft"]),
        "warn": (c["warn"], c["warn_soft"]),
        "danger": (c["danger"], c["danger_soft"]),
        "info": (c["info"], c["info_soft"]),
        "accent": (c["accent"], c["accent_soft"]),
        "paid": (c["gpu_paid"], c["accent_soft"]),
        "free": (c["gpu_free"], c["info_soft"]),
    }.get(tone, (c["muted"], c["track"]))


def state_tone(state):
    """Tone for a Slurm job or node state word."""
    s = (state or "").upper()
    if s.startswith(("RUNNING", "IDLE", "READY", "COMPLETED")):
        return "ok"
    if s.startswith(("PENDING", "CONFIGURING", "COMPLETING", "MIXED", "STARTING")):
        return "warn" if s.startswith("PENDING") else "info"
    if s.startswith(("FAILED", "TIMEOUT", "CANCELLED", "NODE_FAIL", "DOWN", "DRAIN",
                     "OUT_OF_MEMORY", "PREEMPTED", "BOOT_FAIL", "ERROR")):
        return "danger"
    return "neutral"


# --- light or dark ---------------------------------------------------------------

def _settings():
    return QSettings("MolloiLab", "HPC3 Launcher")


def saved_mode():
    """The remembered choice: "system", "light" or "dark"."""
    mode = str(_settings().value(SETTINGS_KEY, "system") or "system").lower()
    return mode if mode in MODES else "system"


def current_mode():
    """What the app is following now (the env override reads as that fixed mode)."""
    forced = os.environ.get("HPC3_THEME", "").lower()
    return forced if forced in ("light", "dark") else saved_mode()


def _os_prefers_dark(app):
    """Ask the operating system, in whatever way it can be asked.

    Qt 5 only mirrors the OS setting into the palette on macOS, and once this
    module has installed its own palette that is gone too -- so Windows is read
    from the registry, macOS from ``defaults``, and elsewhere the palette Qt
    started with is remembered.
    """
    global _startup_dark
    if _startup_dark is None:
        _startup_dark = app.palette().color(QPalette.Window).lightness() < 128
    try:
        if sys.platform == "win32":
            import winreg
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            try:
                return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
            finally:
                winreg.CloseKey(key)
        if sys.platform == "darwin":
            out = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"],
                                 capture_output=True, text=True, timeout=2)
            return out.stdout.strip().lower() == "dark"
    except Exception:       # no such key, no `defaults`: fall back to what Qt saw
        pass
    return _startup_dark


def _wants_dark(app, mode):
    forced = os.environ.get("HPC3_THEME", "").lower()
    if forced in ("light", "dark"):
        return forced == "dark"
    if mode in ("light", "dark"):
        return mode == "dark"
    return _os_prefers_dark(app)


def set_mode(mode, app=None):
    """Switch to "system", "light" or "dark", remember it, and restyle the app."""
    if mode not in MODES:
        raise ValueError(f"unknown theme mode: {mode!r}")
    settings = _settings()
    settings.setValue(SETTINGS_KEY, mode)
    settings.sync()
    apply_theme(app, mode)


# --- cursors, fonts, window chrome ------------------------------------------------

def mono_font(pixel_size=12):
    """The platform's own monospace face (Menlo is a Mac font; asking Windows for
    it gets Courier)."""
    wanted = {"darwin": ("SF Mono", "Menlo", "Monaco"),
              "win32": ("Cascadia Mono", "Cascadia Code", "Consolas")}.get(
                  sys.platform, ("DejaVu Sans Mono", "Liberation Mono", "Noto Sans Mono"))
    families = set(QFontDatabase().families())
    font = QFont(next((f for f in wanted if f in families), wanted[-1]))
    font.setStyleHint(QFont.Monospace)
    font.setPixelSize(pixel_size)
    return font


def nav_icon(name, size=18):
    """A thin line icon for the sidebar ("sessions", "jobs", "nodes", "balance")."""
    from PyQt5.QtCore import QRectF
    from PyQt5.QtGui import QIcon
    icon = QIcon()
    for mode, key in ((QIcon.Normal, "sidebar_text"), (QIcon.Selected, "sidebar_active")):
        px = QPixmap(size * 2, size * 2)
        px.fill(Qt.transparent)
        p = QPainter(px)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(size * 2 / 18.0, size * 2 / 18.0)       # draw on an 18-unit grid
        pen = QPen(QColor(_current[key]))
        pen.setWidthF(1.5)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        if name == "sessions":          # a terminal window with a prompt
            p.drawRoundedRect(QRectF(2, 3, 14, 12), 2.5, 2.5)
            p.drawPolyline(QPointF(5.5, 7), QPointF(8, 9), QPointF(5.5, 11))
            p.drawLine(QPointF(9.5, 11.5), QPointF(12.5, 11.5))
        elif name == "jobs":            # a list
            for y in (5, 9, 13):
                p.drawPoint(QPointF(3.5, y))
                p.drawLine(QPointF(7, y), QPointF(15, y))
        elif name == "nodes":           # four units in a rack
            for x, y in ((2.5, 2.5), (10, 2.5), (2.5, 10), (10, 10)):
                p.drawRoundedRect(QRectF(x, y, 5.5, 5.5), 1.5, 1.5)
        else:                           # balance: a bar chart
            for x, top in ((4, 10), (9, 5), (14, 8)):
                p.drawLine(QPointF(x, 15), QPointF(x, top))
        p.end()
        px.setDevicePixelRatio(2.0)
        icon.addPixmap(px, mode)
    return icon


def _style_window(widget):
    """Windows only: make the native title bar dark when the theme is.

    Qt 5 leaves the title bar light, which frames a dark window in white.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        value = ctypes.c_int(1 if _current["name"] == "dark" else 0)
        hwnd = int(widget.winId())
        for attribute in (20, 19):      # DWMWA_USE_IMMERSIVE_DARK_MODE (19 before 20H1)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except Exception:
        pass


class _Polisher(QObject):
    """Application-wide: pointing-hand cursors on what can be clicked.

    Done here once rather than on each widget, so a control added later cannot be
    forgotten. A disabled control gets the plain arrow back.
    """

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind in (QEvent.Polish, QEvent.EnabledChange, QEvent.DynamicPropertyChange):
            if isinstance(obj, (QAbstractButton, QComboBox, QTabBar)):
                obj.setCursor(Qt.PointingHandCursor if obj.isEnabled() else Qt.ArrowCursor)
            elif isinstance(obj, QAbstractItemView) and not isinstance(obj, QHeaderView):
                # Rows point where clicking one does something: a table marked
                # ``clickableRows``, the sidebar, a combo box's drop-down list.
                # (Sortable headers get theirs in components.make_table.)
                parent = obj.parent()
                if (obj.property("clickableRows") or obj.objectName() == "nav"
                        or (parent is not None
                            and parent.inherits("QComboBoxPrivateContainer"))):
                    obj.viewport().setCursor(Qt.PointingHandCursor)
        elif kind == QEvent.Show and isinstance(obj, QWidget) and obj.isWindow():
            _style_window(obj)
        return False


def _chevron(path, direction, hex_color, size=10):
    """Draw a small chevron PNG (at 1x and 2x) for combo boxes and spin boxes."""
    for scale, suffix in ((1, ""), (2, "@2x")):
        px = QPixmap(size * scale, size * scale)
        px.fill(Qt.transparent)
        p = QPainter(px)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(hex_color))
        pen.setWidthF(1.6 * scale)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        s = size * scale
        if direction == "down":
            pts = [QPointF(0.22 * s, 0.38 * s), QPointF(0.5 * s, 0.66 * s), QPointF(0.78 * s, 0.38 * s)]
        else:
            pts = [QPointF(0.22 * s, 0.62 * s), QPointF(0.5 * s, 0.34 * s), QPointF(0.78 * s, 0.62 * s)]
        p.drawPolyline(*pts)
        p.end()
        root, ext = os.path.splitext(path)
        px.save(f"{root}{suffix}{ext}", "PNG")


def _check(path, hex_color, size=12):
    for scale, suffix in ((1, ""), (2, "@2x")):
        px = QPixmap(size * scale, size * scale)
        px.fill(Qt.transparent)
        p = QPainter(px)
        p.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(hex_color))
        pen.setWidthF(1.9 * scale)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        s = size * scale
        p.drawPolyline(QPointF(0.22 * s, 0.52 * s), QPointF(0.43 * s, 0.72 * s),
                       QPointF(0.79 * s, 0.30 * s))
        p.end()
        root, ext = os.path.splitext(path)
        px.save(f"{root}{suffix}{ext}", "PNG")


def _build_assets(c):
    """Render the handful of glyphs the stylesheet references, into a temp dir."""
    global _asset_dir
    if _asset_dir is None:
        _asset_dir = tempfile.mkdtemp(prefix="hpc3-launcher-ui-")
    files = {}
    for name, direction, key in (("down", "down", "muted"), ("up", "up", "muted"),
                                 ("down_off", "down", "faint"), ("up_off", "up", "faint")):
        path = os.path.join(_asset_dir, f"{c['name']}_{name}.png")
        _chevron(path, direction, c[key])
        files[name] = path.replace("\\", "/")
    path = os.path.join(_asset_dir, f"{c['name']}_check.png")
    _check(path, c["on_accent"])
    files["check"] = path.replace("\\", "/")
    return files


def build_stylesheet(c, assets):
    mono = f'"{mono_font().family()}", monospace'   # a face this machine really has
    return f"""
* {{ font-size: 14px; }}
QWidget {{ color: {c['text']}; }}
QMainWindow, QDialog, QWidget#page, QStackedWidget {{ background: {c['bg']}; }}
QToolTip {{ background: {c['text']}; color: {c['bg']}; border: none; padding: 5px 8px; }}

/* --- text roles --- */
QLabel {{ background: transparent; }}
QLabel[role="title"] {{ font-size: 15px; font-weight: 600; }}
QLabel[role="heading"] {{ font-size: 22px; font-weight: 600; }}
QLabel[role="subtitle"] {{ color: {c['muted']}; }}
QLabel[role="section"] {{ font-size: 12px; color: {c['muted']}; }}
QLabel[role="muted"] {{ color: {c['muted']}; }}
QLabel[role="faint"] {{ color: {c['faint']}; font-size: 13px; }}
QLabel[role="field"] {{ color: {c['muted']}; font-size: 13px; }}
QLabel[role="stat"] {{ font-size: 24px; font-weight: 600; }}
QLabel[role="mono"] {{ font-family: {mono}; font-size: 13px; }}
QLabel[role="summary"] {{ color: {c['muted']}; font-size: 13px; }}
QLabel[tone="ok"] {{ color: {c['ok']}; }}
QLabel[tone="warn"] {{ color: {c['warn']}; }}
QLabel[tone="danger"] {{ color: {c['danger']}; }}
QLabel[tone="accent"] {{ color: {c['accent']}; }}
QLabel[tone="muted"] {{ color: {c['muted']}; }}

/* --- status dots: the one place a state gets a colour --- */
QLabel[dot] {{ border-radius: 4px; min-width: 8px; max-width: 8px; min-height: 8px;
    max-height: 8px; background: {c['border_strong']}; }}
QLabel[dot="ok"] {{ background: {c['ok']}; }}
QLabel[dot="warn"] {{ background: {c['warn']}; }}
QLabel[dot="danger"] {{ background: {c['danger']}; }}
QLabel[dot="accent"] {{ background: {c['accent']}; }}
QLabel[dot="info"] {{ background: {c['info']}; }}
QLabel[dot="gpu_paid"] {{ background: {c['gpu_paid']}; }}
QLabel[dot="gpu_free"] {{ background: {c['gpu_free']}; }}
QLabel[dot="gpu_idle"] {{ background: {c['gpu_idle']}; }}

/* --- one kind of surface: a soft floating panel. Everything else is the canvas. --- */
QFrame[card="true"] {{ background: {c['surface']}; border: 1px solid {c['border']};
    border-radius: 14px; }}
QFrame[card="flat"] {{ background: transparent; border: none; }}
QFrame[card="soft"] {{ background: {c['surface2']}; border: none; border-radius: 10px; }}
QFrame[card="accent"] {{ background: {c['accent_soft']}; border: 1px solid {c['accent_soft']};
    border-radius: 8px; }}
QFrame[divider="true"] {{ background: {c['border']}; border: none; max-height: 1px; min-height: 1px; }}

/* --- sidebar --- */
QWidget#sidebar {{ background: {c['sidebar']}; }}
QWidget#sidebar QLabel {{ color: {c['sidebar_text']}; font-size: 13px; }}
QWidget#sidebar QLabel#brand {{ color: {c['sidebar_active']}; font-size: 15px; font-weight: 600; }}
QWidget#sidebar QLabel#user {{ color: {c['sidebar_active']}; font-size: 14px; font-weight: 500; }}
QListWidget#nav {{ background: transparent; border: none; outline: none; padding: 0; }}
QListWidget#nav::item {{ color: {c['sidebar_text']}; padding: 9px 10px; margin: 1px 10px;
    border-radius: 9px; }}
QListWidget#nav::item:hover {{ background: {c['sidebar_hover']}; color: {c['sidebar_active']}; }}
QListWidget#nav::item:selected {{ background: {c['sidebar_selected']}; color: {c['sidebar_active']};
    font-weight: 600; }}

/* --- buttons --- */
QPushButton {{ background: {c['track']}; border: 1px solid transparent;
    border-radius: 9px; padding: 6px 12px; font-weight: 500; min-height: 20px; }}
QPushButton:hover {{ background: {c['border_strong']}; }}
QPushButton:pressed {{ background: {c['border_strong']}; }}
QPushButton:disabled {{ color: {c['faint']}; background: transparent; }}
QPushButton[variant="primary"] {{ background: {c['accent']}; border-color: {c['accent']};
    color: {c['on_accent']}; font-weight: 600; border-radius: 10px; padding: 8px 14px; }}
QPushButton[variant="primary"]:hover {{ background: {c['accent_hover']}; border-color: {c['accent_hover']}; }}
QPushButton[variant="primary"]:disabled {{ background: {c['track']}; border-color: {c['track']};
    color: {c['faint']}; }}
QPushButton[variant="danger"] {{ color: {c['danger']}; background: transparent; }}
QPushButton[variant="danger"]:hover {{ background: {c['danger_soft']}; }}
QPushButton[variant="danger"]:disabled {{ color: {c['faint']}; }}
QPushButton[variant="ghost"] {{ background: transparent; border-color: transparent; color: {c['muted']};
    padding: 4px 8px; }}
QPushButton[variant="ghost"]:hover {{ background: {c['track']}; color: {c['text']}; }}
QPushButton[variant="ghost"]:disabled {{ color: {c['faint']}; background: transparent; }}
QPushButton[variant="link"] {{ background: transparent; border-color: transparent; color: {c['info']};
    padding: 2px 6px; }}
QPushButton[variant="link"]:hover {{ background: {c['accent_soft']}; }}
QPushButton[variant="tile"] {{ text-align: left; padding: 8px 12px; font-weight: 400;
    background: {c['surface2']}; border-radius: 10px; }}
QPushButton[variant="tile"]:hover {{ background: {c['track']}; }}
QPushButton[variant="tile"]:checked {{ background: {c['accent_soft']}; color: {c['text']}; }}
QPushButton[variant="tile"]:disabled {{ color: {c['faint']}; }}
QPushButton[segment="true"] {{ background: transparent; border: 1px solid transparent;
    border-radius: 6px; padding: 4px 12px; color: {c['muted']}; font-weight: 500; }}
QPushButton[segment="true"]:hover {{ color: {c['text']}; background: transparent; }}
QPushButton[segment="true"]:checked {{ background: {c['surface2']}; color: {c['text']}; }}
QPushButton[segment="true"]:disabled {{ color: {c['faint']}; background: transparent; }}
QFrame#segmented {{ background: {c['track']}; border-radius: 9px; }}
QToolButton {{ background: transparent; border: none; color: {c['muted']}; padding: 4px 6px;
    border-radius: 6px; }}
QToolButton:hover {{ color: {c['text']}; background: {c['track']}; }}

/* --- inputs --- */
QLineEdit, QComboBox, QSpinBox, QTextEdit, QPlainTextEdit {{ background: {c['surface2']};
    border: 1px solid transparent; border-radius: 9px; padding: 6px 10px;
    selection-background-color: {c['accent']}; selection-color: {c['on_accent']}; }}
QLineEdit, QComboBox, QSpinBox {{ min-height: 22px; }}
QComboBox:hover, QSpinBox:hover, QLineEdit:hover {{ border-color: {c['border_strong']}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border-color: {c['accent']}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{ background: transparent;
    color: {c['faint']}; border-color: {c['border']}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: url("{assets['down']}"); width: 10px; height: 10px; }}
QComboBox::down-arrow:disabled {{ image: url("{assets['down_off']}"); }}
QComboBox QAbstractItemView {{ background: {c['surface2']}; border: 1px solid {c['border']};
    border-radius: 10px; padding: 4px; outline: none;
    selection-background-color: {c['accent_soft']}; selection-color: {c['text']}; }}
QComboBox QAbstractItemView::item {{ padding: 6px 8px; border-radius: 6px; min-height: 20px; }}
QComboBox QAbstractItemView::item:disabled {{ color: {c['faint']}; }}
QSpinBox::up-button, QSpinBox::down-button {{ border: none; width: 22px; background: transparent; }}
QSpinBox::up-arrow {{ image: url("{assets['up']}"); width: 10px; height: 10px; }}
QSpinBox::down-arrow {{ image: url("{assets['down']}"); width: 10px; height: 10px; }}
QSpinBox::up-arrow:disabled, QSpinBox::up-arrow:off {{ image: url("{assets['up_off']}"); }}
QSpinBox::down-arrow:disabled, QSpinBox::down-arrow:off {{ image: url("{assets['down_off']}"); }}
QTextEdit, QPlainTextEdit {{ background: {c['surface2']}; }}
QTextEdit[flat="true"] {{ background: transparent; border: none; padding: 0; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 15px; height: 15px; border-radius: 4px;
    border: 1px solid {c['border_strong']}; background: {c['surface']}; }}
QCheckBox::indicator:hover {{ border-color: {c['faint']}; }}
QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']};
    image: url("{assets['check']}"); }}

/* --- tables are lists: rows straight on the canvas, no box, no header bar --- */
QTableWidget, QTableView {{ background: transparent; border: none;
    gridline-color: transparent; outline: none;
    selection-background-color: {c['accent_soft']}; selection-color: {c['text']};
    alternate-background-color: transparent; }}
QTableWidget::item, QTableView::item {{ padding: 4px 10px; border: none;
    border-bottom: 1px solid {c['border']}; }}
QTableWidget::item:hover, QTableView::item:hover {{ background: {c['track']}; }}
QTableWidget::item:selected, QTableView::item:selected {{ background: {c['accent_soft']};
    color: {c['text']}; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{ background: transparent; color: {c['faint']}; border: none;
    padding: 6px 10px; font-size: 12px; }}
QHeaderView::section:hover {{ color: {c['text']}; }}
QTableCornerButton::section {{ background: transparent; border: none; }}
QListWidget {{ background: transparent; border: none; padding: 0; outline: none; }}
QListWidget::item {{ padding: 9px 10px; border-radius: 9px; }}
QListWidget::item:hover {{ background: {c['track']}; }}
QListWidget::item:selected {{ background: {c['accent_soft']}; color: {c['text']}; }}

/* --- tabs --- */
QTabWidget::pane {{ border: none; top: 0px; }}
QTabWidget QStackedWidget {{ background: transparent; }}
QTabBar {{ background: transparent; font-weight: 500; }}
QTabBar::tab {{ background: transparent; color: {c['muted']}; padding: 7px 2px; margin-right: 20px;
    border: none; border-bottom: 2px solid transparent; }}
QTabBar::tab:hover {{ color: {c['text']}; }}
QTabBar::tab:selected {{ color: {c['text']}; border-bottom: 2px solid {c['text']}; }}

/* --- scrollbars: thin, and only the thumb --- */
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 1px; }}
QScrollBar::handle:vertical {{ background: {c['border_strong']}; border-radius: 3px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {c['faint']}; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 1px; }}
QScrollBar::handle:horizontal {{ background: {c['border_strong']}; border-radius: 3px; min-width: 28px; }}
QScrollBar::handle:horizontal:hover {{ background: {c['faint']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* --- the rest --- */
QGroupBox {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 14px;
    margin-top: 12px; padding: 14px 12px 12px 12px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; color: {c['muted']}; }}
QProgressBar {{ background: {c['track']}; border: none; border-radius: 3px; max-height: 6px;
    min-height: 6px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 3px; }}
QMenuBar {{ background: {c['sidebar']}; }}
QMenuBar::item {{ padding: 4px 10px; background: transparent; border-radius: 6px; }}
QMenuBar::item:selected {{ background: {c['track']}; }}
QMenu {{ background: {c['surface2']}; border: 1px solid {c['border']}; border-radius: 10px;
    padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {c['accent_soft']}; color: {c['text']}; }}
QMenu::separator {{ height: 1px; background: {c['border']}; margin: 4px 8px; }}
QSplitter::handle {{ background: transparent; }}
QMessageBox, QInputDialog {{ background: {c['surface']}; }}
"""


def apply_theme(app=None, mode=None):
    """Install the style, palette and stylesheet.

    Called once right after QApplication, and again by ``set_mode`` whenever the
    user switches between light and dark.
    """
    global _current, _filter
    app = app or QApplication.instance()
    dark = _wants_dark(app, mode or saved_mode())
    first = _filter is None
    _current = dict(DARK if dark else LIGHT)
    c = _current

    if first:
        app.setStyle("Fusion")
        _filter = _Polisher(app)
        app.installEventFilter(_filter)
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(c["bg"]))
    pal.setColor(QPalette.WindowText, QColor(c["text"]))
    pal.setColor(QPalette.Base, QColor(c["surface"]))
    pal.setColor(QPalette.AlternateBase, QColor(c["surface2"]))
    pal.setColor(QPalette.Text, QColor(c["text"]))
    pal.setColor(QPalette.Button, QColor(c["surface"]))
    pal.setColor(QPalette.ButtonText, QColor(c["text"]))
    pal.setColor(QPalette.ToolTipBase, QColor(c["text"]))
    pal.setColor(QPalette.ToolTipText, QColor(c["bg"]))
    pal.setColor(QPalette.Highlight, QColor(c["accent"]))
    pal.setColor(QPalette.HighlightedText, QColor(c["on_accent"]))
    pal.setColor(QPalette.PlaceholderText, QColor(c["faint"]))
    pal.setColor(QPalette.Link, QColor(c["accent"]))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor(c["faint"]))
    app.setPalette(pal)
    app.setStyleSheet(build_stylesheet(c, _build_assets(c)))
    if not first:
        for window in app.topLevelWidgets():
            if window.isVisible():
                _style_window(window)
        notifier.changed.emit()
    return c


def restyle(widget):
    """Re-evaluate the stylesheet after changing a dynamic property."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()
