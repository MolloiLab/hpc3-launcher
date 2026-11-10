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

The look is deliberately quiet: a neutral canvas, hairline borders, one accent
colour, and status shown as a small coloured dot beside plain text rather than
as coloured paragraphs. (The palette follows T3 Code's zinc/neutral tokens.)

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
    "bg": "#FCFCFC", "surface": "#FFFFFF", "surface2": "#FAFAFA",
    "border": "#E4E4E7", "border_strong": "#D4D4D8",
    "text": "#18181B", "muted": "#71717A", "faint": "#A1A1AA",
    "accent": "#2F54D6", "accent_hover": "#2546BC", "accent_soft": "#EEF2FF",
    "on_accent": "#FFFFFF",
    "ok": "#059669", "ok_soft": "#ECFDF5",
    "warn": "#B45309", "warn_soft": "#FFFBEB",
    "danger": "#DC2626", "danger_soft": "#FEF2F2",
    "info": "#2563EB", "info_soft": "#EFF6FF",
    # The sidebar is the canvas, set apart by a hairline (not a dark slab).
    "sidebar": "#FAFAFA", "sidebar_text": "#71717A", "sidebar_active": "#18181B",
    "sidebar_hover": "#F4F4F5", "sidebar_selected": "#EDEDEF",
    # Who holds a GPU. Paid and free are deliberately far apart in hue.
    "gpu_paid": "#7C6CF2", "gpu_free": "#38BDF8", "gpu_idle": "#10B981",
    "track": "#F1F1F3",
}

DARK = {
    "name": "dark",
    "bg": "#050505", "surface": "#111111", "surface2": "#171717",
    "border": "#242424", "border_strong": "#303030",
    "text": "#F5F5F5", "muted": "#A3A3A3", "faint": "#6B6B6B",
    "accent": "#5B7CF7", "accent_hover": "#7491FA", "accent_soft": "#171D38",
    "on_accent": "#FFFFFF",
    "ok": "#34D399", "ok_soft": "#0C2A20",
    "warn": "#FBBF24", "warn_soft": "#2E230A",
    "danger": "#F87171", "danger_soft": "#2D1414",
    "info": "#60A5FA", "info_soft": "#101F38",
    "sidebar": "#050505", "sidebar_text": "#A3A3A3", "sidebar_active": "#F5F5F5",
    "sidebar_hover": "#141414", "sidebar_selected": "#1C1C1C",
    "gpu_paid": "#8E80F5", "gpu_free": "#38BDF8", "gpu_idle": "#34D399",
    "track": "#1F1F1F",
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
* {{ font-size: 13px; }}
QWidget {{ color: {c['text']}; }}
QMainWindow, QDialog, QWidget#page, QStackedWidget {{ background: {c['bg']}; }}
QToolTip {{ background: {c['text']}; color: {c['bg']}; border: none; padding: 5px 8px; }}

/* --- text roles --- */
QLabel {{ background: transparent; }}
QLabel[role="title"] {{ font-size: 20px; font-weight: 600; }}
QLabel[role="subtitle"] {{ color: {c['muted']}; }}
QLabel[role="section"] {{ font-size: 13px; font-weight: 600; }}
QLabel[role="muted"] {{ color: {c['muted']}; }}
QLabel[role="faint"] {{ color: {c['faint']}; font-size: 12px; }}
QLabel[role="field"] {{ color: {c['muted']}; font-size: 12px; }}
QLabel[role="stat"] {{ font-size: 22px; font-weight: 600; }}
QLabel[role="mono"] {{ font-family: {mono}; font-size: 12px; }}
QLabel[role="summary"] {{ color: {c['muted']}; font-size: 12px; }}
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

/* --- cards: one hairline, never nested --- */
QFrame[card="true"] {{ background: {c['surface']}; border: 1px solid {c['border']};
    border-radius: 10px; }}
QFrame[card="soft"] {{ background: {c['surface2']}; border: 1px solid {c['border']};
    border-radius: 8px; }}
QFrame[card="accent"] {{ background: {c['accent_soft']}; border: 1px solid {c['accent_soft']};
    border-radius: 8px; }}
QFrame[divider="true"] {{ background: {c['border']}; border: none; max-height: 1px; min-height: 1px; }}

/* --- sidebar --- */
QWidget#sidebar {{ background: {c['sidebar']}; border-right: 1px solid {c['border']}; }}
QWidget#sidebar QLabel {{ color: {c['sidebar_text']}; font-size: 12px; }}
QWidget#sidebar QLabel#brand {{ color: {c['sidebar_active']}; font-size: 14px; font-weight: 600; }}
QWidget#sidebar QLabel#user {{ color: {c['sidebar_active']}; font-size: 13px; font-weight: 500; }}
QListWidget#nav {{ background: transparent; border: none; outline: none; padding: 0; }}
QListWidget#nav::item {{ color: {c['sidebar_text']}; padding: 7px 10px; margin: 1px 10px;
    border-radius: 6px; }}
QListWidget#nav::item:hover {{ background: {c['sidebar_hover']}; color: {c['sidebar_active']}; }}
QListWidget#nav::item:selected {{ background: {c['sidebar_selected']}; color: {c['sidebar_active']};
    font-weight: 500; }}

/* --- buttons --- */
QPushButton {{ background: {c['surface']}; border: 1px solid {c['border']};
    border-radius: 8px; padding: 6px 12px; font-weight: 500; min-height: 18px; }}
QPushButton:hover {{ background: {c['track']}; }}
QPushButton:pressed {{ background: {c['border']}; }}
QPushButton:disabled {{ color: {c['faint']}; background: transparent; }}
QPushButton[variant="primary"] {{ background: {c['accent']}; border-color: {c['accent']};
    color: {c['on_accent']}; font-weight: 600; }}
QPushButton[variant="primary"]:hover {{ background: {c['accent_hover']}; border-color: {c['accent_hover']}; }}
QPushButton[variant="primary"]:disabled {{ background: {c['track']}; border-color: {c['track']};
    color: {c['faint']}; }}
QPushButton[variant="danger"] {{ color: {c['danger']}; }}
QPushButton[variant="danger"]:hover {{ background: {c['danger_soft']}; }}
QPushButton[variant="danger"]:disabled {{ color: {c['faint']}; }}
QPushButton[variant="ghost"] {{ background: transparent; border-color: transparent; color: {c['muted']};
    padding: 4px 8px; }}
QPushButton[variant="ghost"]:hover {{ background: {c['track']}; color: {c['text']}; }}
QPushButton[variant="ghost"]:disabled {{ color: {c['faint']}; background: transparent; }}
QPushButton[variant="link"] {{ background: transparent; border-color: transparent; color: {c['accent']};
    padding: 2px 6px; }}
QPushButton[variant="link"]:hover {{ background: {c['accent_soft']}; }}
QPushButton[variant="tile"] {{ text-align: left; padding: 7px 10px; font-weight: 400; }}
QPushButton[variant="tile"]:checked {{ border-color: {c['accent']}; background: {c['accent_soft']};
    color: {c['text']}; }}
QPushButton[variant="tile"]:disabled {{ color: {c['faint']}; }}
QPushButton[segment="true"] {{ background: transparent; border: 1px solid transparent;
    border-radius: 6px; padding: 4px 12px; color: {c['muted']}; font-weight: 500; }}
QPushButton[segment="true"]:hover {{ color: {c['text']}; background: transparent; }}
QPushButton[segment="true"]:checked {{ background: {c['surface']}; color: {c['text']};
    border: 1px solid {c['border']}; }}
QFrame#segmented {{ background: {c['track']}; border-radius: 8px; }}
QToolButton {{ background: transparent; border: none; color: {c['muted']}; padding: 4px 6px;
    border-radius: 6px; }}
QToolButton:hover {{ color: {c['text']}; background: {c['track']}; }}

/* --- inputs --- */
QLineEdit, QComboBox, QSpinBox, QTextEdit, QPlainTextEdit {{ background: {c['surface']};
    border: 1px solid {c['border_strong']}; border-radius: 8px; padding: 5px 10px;
    selection-background-color: {c['accent']}; selection-color: {c['on_accent']}; }}
QLineEdit, QComboBox, QSpinBox {{ min-height: 20px; }}
QComboBox:hover, QSpinBox:hover, QLineEdit:hover {{ border-color: {c['faint']}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border-color: {c['accent']}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{ background: {c['surface2']};
    color: {c['faint']}; border-color: {c['border']}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: url("{assets['down']}"); width: 10px; height: 10px; }}
QComboBox::down-arrow:disabled {{ image: url("{assets['down_off']}"); }}
QComboBox QAbstractItemView {{ background: {c['surface']}; border: 1px solid {c['border_strong']};
    border-radius: 8px; padding: 4px; outline: none;
    selection-background-color: {c['track']}; selection-color: {c['text']}; }}
QComboBox QAbstractItemView::item {{ padding: 6px 8px; border-radius: 6px; min-height: 20px; }}
QComboBox QAbstractItemView::item:disabled {{ color: {c['faint']}; }}
QSpinBox::up-button, QSpinBox::down-button {{ border: none; width: 22px; background: transparent; }}
QSpinBox::up-arrow {{ image: url("{assets['up']}"); width: 10px; height: 10px; }}
QSpinBox::down-arrow {{ image: url("{assets['down']}"); width: 10px; height: 10px; }}
QSpinBox::up-arrow:disabled, QSpinBox::up-arrow:off {{ image: url("{assets['up_off']}"); }}
QSpinBox::down-arrow:disabled, QSpinBox::down-arrow:off {{ image: url("{assets['down_off']}"); }}
QTextEdit, QPlainTextEdit {{ background: {c['surface2']}; border-color: {c['border']}; }}
QTextEdit[flat="true"] {{ background: transparent; border: none; padding: 0; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 15px; height: 15px; border-radius: 4px;
    border: 1px solid {c['border_strong']}; background: {c['surface']}; }}
QCheckBox::indicator:hover {{ border-color: {c['faint']}; }}
QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']};
    image: url("{assets['check']}"); }}

/* --- tables: rows separated by a hairline, no box of their own inside a card --- */
QTableWidget, QTableView {{ background: {c['surface']}; border: 1px solid {c['border']};
    border-radius: 10px; gridline-color: transparent; outline: none;
    selection-background-color: {c['track']}; selection-color: {c['text']};
    alternate-background-color: {c['surface']}; }}
QFrame[card="true"] QTableWidget, QFrame[card="true"] QTableView {{ border: none;
    border-radius: 0; background: transparent; }}
QTableWidget::item, QTableView::item {{ padding: 4px 10px; border: none;
    border-bottom: 1px solid {c['border']}; }}
QTableWidget::item:hover, QTableView::item:hover {{ background: {c['surface2']}; }}
QTableWidget::item:selected, QTableView::item:selected {{ background: {c['track']};
    color: {c['text']}; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{ background: transparent; color: {c['muted']}; border: none;
    border-bottom: 1px solid {c['border']}; padding: 7px 10px; font-size: 12px; }}
QHeaderView::section:hover {{ color: {c['text']}; }}
QTableCornerButton::section {{ background: transparent; border: none; }}
QListWidget {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 10px;
    padding: 4px; outline: none; }}
QFrame[card="true"] QListWidget {{ border: none; padding: 0; background: transparent; }}
QListWidget::item {{ padding: 8px 10px; border-radius: 6px; }}
QListWidget::item:hover {{ background: {c['surface2']}; }}
QListWidget::item:selected {{ background: {c['track']}; color: {c['text']}; }}

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
QGroupBox {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 10px;
    margin-top: 12px; padding: 14px 12px 12px 12px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; color: {c['muted']}; }}
QProgressBar {{ background: {c['track']}; border: none; border-radius: 3px; max-height: 6px;
    min-height: 6px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 3px; }}
QMenuBar {{ background: {c['sidebar']}; border-bottom: 1px solid {c['border']}; }}
QMenuBar::item {{ padding: 4px 10px; background: transparent; border-radius: 6px; }}
QMenuBar::item:selected {{ background: {c['track']}; }}
QMenu {{ background: {c['surface']}; border: 1px solid {c['border_strong']}; border-radius: 8px;
    padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {c['track']}; color: {c['text']}; }}
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
