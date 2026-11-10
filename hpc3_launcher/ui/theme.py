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

The theme follows the OS (light or dark) at launch; ``HPC3_THEME=light|dark``
overrides it.
"""

import os
import tempfile

from PyQt5.QtCore import Qt, QPointF
from PyQt5.QtGui import QColor, QPalette, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import QApplication

LIGHT = {
    "name": "light",
    "bg": "#F4F5F7", "surface": "#FFFFFF", "surface2": "#F8F9FB",
    "border": "#E2E5EA", "border_strong": "#C9CED6",
    "text": "#111827", "muted": "#667085", "faint": "#98A2B3",
    "accent": "#4F46E5", "accent_hover": "#4338CA", "accent_soft": "#EEF0FE",
    "on_accent": "#FFFFFF",
    "ok": "#15803D", "ok_soft": "#E7F6EC",
    "warn": "#B45309", "warn_soft": "#FDF3E1",
    "danger": "#D92D20", "danger_soft": "#FDECEA",
    "info": "#0369A1", "info_soft": "#E3F2FB",
    "sidebar": "#101422", "sidebar_text": "#A6AEC0", "sidebar_active": "#FFFFFF",
    "sidebar_hover": "#1A2033", "sidebar_selected": "#252C45",
    # Who holds a GPU. Paid and free are deliberately far apart in hue.
    "gpu_paid": "#7C3AED", "gpu_free": "#0EA5E9", "gpu_idle": "#22C55E",
    "track": "#E9ECF1",
}

DARK = {
    "name": "dark",
    "bg": "#0E1116", "surface": "#161A22", "surface2": "#1B2029",
    "border": "#272D38", "border_strong": "#3A4352",
    "text": "#E7EAF0", "muted": "#98A2B3", "faint": "#667085",
    "accent": "#818CF8", "accent_hover": "#A5B4FC", "accent_soft": "#23274A",
    "on_accent": "#0E1116",
    "ok": "#4ADE80", "ok_soft": "#12301F",
    "warn": "#FBBF24", "warn_soft": "#3A2A0B",
    "danger": "#F87171", "danger_soft": "#3B1717",
    "info": "#38BDF8", "info_soft": "#0C2C3D",
    "sidebar": "#0A0D12", "sidebar_text": "#8B93A5", "sidebar_active": "#FFFFFF",
    "sidebar_hover": "#141922", "sidebar_selected": "#1E2433",
    "gpu_paid": "#A78BFA", "gpu_free": "#38BDF8", "gpu_idle": "#4ADE80",
    "track": "#262C37",
}

_current = dict(LIGHT)
_asset_dir = None


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


def _system_prefers_dark(app):
    forced = os.environ.get("HPC3_THEME", "").lower()
    if forced in ("light", "dark"):
        return forced == "dark"
    return app.palette().color(QPalette.Window).lightness() < 128


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
    return f"""
* {{ font-size: 13px; }}
QWidget {{ color: {c['text']}; }}
QMainWindow, QDialog, QWidget#page, QStackedWidget {{ background: {c['bg']}; }}
QToolTip {{ background: {c['text']}; color: {c['surface']}; border: none; padding: 5px 8px; }}

/* --- text roles --- */
QLabel {{ background: transparent; }}
QLabel[role="title"] {{ font-size: 22px; font-weight: 600; }}
QLabel[role="subtitle"] {{ color: {c['muted']}; }}
QLabel[role="section"] {{ font-size: 14px; font-weight: 600; }}
QLabel[role="muted"] {{ color: {c['muted']}; }}
QLabel[role="faint"] {{ color: {c['faint']}; font-size: 12px; }}
QLabel[role="field"] {{ color: {c['muted']}; font-size: 12px; font-weight: 600; }}
QLabel[role="stat"] {{ font-size: 26px; font-weight: 600; }}
QLabel[role="mono"] {{ font-family: Menlo, Consolas, "DejaVu Sans Mono", monospace; font-size: 12px; }}
QLabel[role="summary"] {{ background: {c['surface2']}; border: 1px solid {c['border']};
    border-radius: 8px; padding: 9px 12px; }}
QLabel[tone="ok"] {{ color: {c['ok']}; }}
QLabel[tone="warn"] {{ color: {c['warn']}; }}
QLabel[tone="danger"] {{ color: {c['danger']}; }}
QLabel[tone="accent"] {{ color: {c['accent']}; }}

/* --- cards --- */
QFrame[card="true"] {{ background: {c['surface']}; border: 1px solid {c['border']};
    border-radius: 12px; }}
QFrame[card="soft"] {{ background: {c['surface2']}; border: 1px solid {c['border']};
    border-radius: 10px; }}
QFrame[card="accent"] {{ background: {c['accent_soft']}; border: 1px solid {c['accent_soft']};
    border-radius: 10px; }}
QFrame[divider="true"] {{ background: {c['border']}; border: none; max-height: 1px; min-height: 1px; }}

/* --- sidebar --- */
QWidget#sidebar {{ background: {c['sidebar']}; }}
QWidget#sidebar QLabel {{ color: {c['sidebar_text']}; }}
QWidget#sidebar QLabel#brand {{ color: {c['sidebar_active']}; font-size: 15px; font-weight: 600; }}
QWidget#sidebar QLabel#user {{ color: {c['sidebar_active']}; font-weight: 600; }}
QListWidget#nav {{ background: transparent; border: none; outline: none; }}
QListWidget#nav::item {{ color: {c['sidebar_text']}; padding: 9px 12px; margin: 1px 10px;
    border-radius: 8px; }}
QListWidget#nav::item:hover {{ background: {c['sidebar_hover']}; color: {c['sidebar_active']}; }}
QListWidget#nav::item:selected {{ background: {c['sidebar_selected']}; color: {c['sidebar_active']};
    font-weight: 600; }}

/* --- buttons --- */
QPushButton {{ background: {c['surface']}; border: 1px solid {c['border_strong']};
    border-radius: 8px; padding: 7px 14px; font-weight: 600; min-height: 18px; }}
QPushButton:hover {{ background: {c['surface2']}; border-color: {c['faint']}; }}
QPushButton:pressed {{ background: {c['track']}; }}
QPushButton:disabled {{ color: {c['faint']}; border-color: {c['border']}; background: {c['surface2']}; }}
QPushButton[variant="primary"] {{ background: {c['accent']}; border-color: {c['accent']};
    color: {c['on_accent']}; }}
QPushButton[variant="primary"]:hover {{ background: {c['accent_hover']}; border-color: {c['accent_hover']}; }}
QPushButton[variant="primary"]:disabled {{ background: {c['track']}; border-color: {c['track']};
    color: {c['faint']}; }}
QPushButton[variant="danger"] {{ color: {c['danger']}; }}
QPushButton[variant="danger"]:hover {{ background: {c['danger_soft']}; border-color: {c['danger']}; }}
QPushButton[variant="danger"]:disabled {{ color: {c['faint']}; }}
QPushButton[variant="ghost"] {{ background: transparent; border-color: transparent; color: {c['accent']};
    padding: 4px 8px; }}
QPushButton[variant="ghost"]:hover {{ background: {c['accent_soft']}; }}
QPushButton[variant="ghost"]:disabled {{ color: {c['faint']}; background: transparent; }}
QPushButton[variant="tile"] {{ text-align: left; padding: 7px 10px; font-weight: 500; }}
QPushButton[variant="tile"]:checked {{ border-color: {c['accent']}; background: {c['accent_soft']};
    color: {c['text']}; }}
QPushButton[variant="tile"]:disabled {{ color: {c['faint']}; }}
QPushButton[segment="true"] {{ background: transparent; border: none; border-radius: 6px;
    padding: 6px 14px; color: {c['muted']}; }}
QPushButton[segment="true"]:hover {{ color: {c['text']}; }}
QPushButton[segment="true"]:checked {{ background: {c['surface']}; color: {c['text']};
    border: 1px solid {c['border_strong']}; }}
QFrame#segmented {{ background: {c['track']}; border-radius: 8px; }}
QToolButton {{ background: transparent; border: none; color: {c['muted']}; padding: 4px 6px;
    border-radius: 6px; }}
QToolButton:hover {{ color: {c['text']}; background: {c['track']}; }}

/* --- inputs --- */
QLineEdit, QComboBox, QSpinBox, QTextEdit, QPlainTextEdit {{ background: {c['surface']};
    border: 1px solid {c['border_strong']}; border-radius: 8px; padding: 6px 10px;
    selection-background-color: {c['accent']}; selection-color: {c['on_accent']}; }}
QLineEdit, QComboBox, QSpinBox {{ min-height: 20px; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border-color: {c['accent']}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{ background: {c['surface2']};
    color: {c['faint']}; border-color: {c['border']}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: url("{assets['down']}"); width: 10px; height: 10px; }}
QComboBox::down-arrow:disabled {{ image: url("{assets['down_off']}"); }}
QComboBox QAbstractItemView {{ background: {c['surface']}; border: 1px solid {c['border_strong']};
    border-radius: 8px; padding: 4px; outline: none;
    selection-background-color: {c['accent_soft']}; selection-color: {c['text']}; }}
QComboBox QAbstractItemView::item {{ padding: 6px 8px; border-radius: 6px; min-height: 20px; }}
QComboBox QAbstractItemView::item:disabled {{ color: {c['faint']}; }}
QSpinBox::up-button, QSpinBox::down-button {{ border: none; width: 22px; background: transparent; }}
QSpinBox::up-arrow {{ image: url("{assets['up']}"); width: 10px; height: 10px; }}
QSpinBox::down-arrow {{ image: url("{assets['down']}"); width: 10px; height: 10px; }}
QSpinBox::up-arrow:disabled, QSpinBox::up-arrow:off {{ image: url("{assets['up_off']}"); }}
QSpinBox::down-arrow:disabled, QSpinBox::down-arrow:off {{ image: url("{assets['down_off']}"); }}
QTextEdit, QPlainTextEdit {{ background: {c['surface2']}; border-color: {c['border']}; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px;
    border: 1px solid {c['border_strong']}; background: {c['surface']}; }}
QCheckBox::indicator:checked {{ background: {c['accent']}; border-color: {c['accent']};
    image: url("{assets['check']}"); }}

/* --- tables --- */
QTableWidget, QTableView {{ background: {c['surface']}; border: 1px solid {c['border']};
    border-radius: 10px; gridline-color: transparent; outline: none;
    selection-background-color: {c['accent_soft']}; selection-color: {c['text']};
    alternate-background-color: {c['surface']}; }}
QTableWidget::item, QTableView::item {{ padding: 4px 10px; border: none;
    border-bottom: 1px solid {c['border']}; }}
QTableWidget::item:selected, QTableView::item:selected {{ background: {c['accent_soft']};
    color: {c['text']}; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{ background: {c['surface2']}; color: {c['muted']}; border: none;
    border-bottom: 1px solid {c['border']}; padding: 8px 10px; font-size: 12px; font-weight: 600; }}
QHeaderView::section:first {{ border-top-left-radius: 10px; }}
QHeaderView::section:last {{ border-top-right-radius: 10px; }}
QTableCornerButton::section {{ background: {c['surface2']}; border: none; }}
QListWidget {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 10px;
    padding: 4px; outline: none; }}
QListWidget::item {{ padding: 8px 10px; border-radius: 6px; }}
QListWidget::item:selected {{ background: {c['accent_soft']}; color: {c['text']}; }}

/* --- tabs --- */
QTabWidget::pane {{ border: none; top: 0px; }}
QTabBar {{ background: transparent; font-weight: 600; }}
QTabBar::tab {{ background: transparent; color: {c['muted']}; padding: 8px 2px; margin-right: 22px;
    border: none; border-bottom: 2px solid transparent; }}
QTabBar::tab:hover {{ color: {c['text']}; }}
QTabBar::tab:selected {{ color: {c['text']}; border-bottom: 2px solid {c['accent']}; }}

/* --- scrollbars --- */
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['border_strong']}; border-radius: 3px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {c['faint']}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {c['border_strong']}; border-radius: 3px; min-width: 28px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* --- the rest --- */
QGroupBox {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 12px;
    margin-top: 12px; padding: 14px 12px 12px 12px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; color: {c['muted']}; }}
QProgressBar {{ background: {c['track']}; border: none; border-radius: 4px; max-height: 8px;
    min-height: 8px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 4px; }}
QMenuBar {{ background: {c['bg']}; }}
QMenuBar::item {{ padding: 4px 10px; background: transparent; border-radius: 6px; }}
QMenuBar::item:selected {{ background: {c['track']}; }}
QMenu {{ background: {c['surface']}; border: 1px solid {c['border_strong']}; border-radius: 8px;
    padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: {c['accent_soft']}; color: {c['text']}; }}
QSplitter::handle {{ background: transparent; }}
QMessageBox {{ background: {c['surface']}; }}
"""


def apply_theme(app=None):
    """Install the style, palette and stylesheet. Call once, right after QApplication."""
    global _current
    app = app or QApplication.instance()
    dark = _system_prefers_dark(app)
    _current = dict(DARK if dark else LIGHT)
    c = _current

    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(c["bg"]))
    pal.setColor(QPalette.WindowText, QColor(c["text"]))
    pal.setColor(QPalette.Base, QColor(c["surface"]))
    pal.setColor(QPalette.AlternateBase, QColor(c["surface2"]))
    pal.setColor(QPalette.Text, QColor(c["text"]))
    pal.setColor(QPalette.Button, QColor(c["surface"]))
    pal.setColor(QPalette.ButtonText, QColor(c["text"]))
    pal.setColor(QPalette.ToolTipBase, QColor(c["text"]))
    pal.setColor(QPalette.ToolTipText, QColor(c["surface"]))
    pal.setColor(QPalette.Highlight, QColor(c["accent"]))
    pal.setColor(QPalette.HighlightedText, QColor(c["on_accent"]))
    pal.setColor(QPalette.PlaceholderText, QColor(c["faint"]))
    pal.setColor(QPalette.Link, QColor(c["accent"]))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor(c["faint"]))
    app.setPalette(pal)
    app.setStyleSheet(build_stylesheet(c, _build_assets(c)))
    return c


def restyle(widget):
    """Re-evaluate the stylesheet after changing a dynamic property."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()
