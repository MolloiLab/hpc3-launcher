#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
A reusable, honest progress surface.

The old app "spun" a label with a CSS ``transform: rotate()`` in a Qt stylesheet
-- which Qt silently ignores, so it never moved -- and elsewhere counted a
progress bar 0->100 over a fixed 20 seconds while waiting for nothing. Both are
gone. This widget gives every slow operation:

  * a spinner that genuinely animates (painted by hand, on a timer)
  * a one-line state + detail label, fed live from a background worker
  * a collapsible log pane so a user (or you, debugging) can watch what's actually
    happening over SSH instead of staring at a frozen window

It pairs with ``core.ssh_session.SSHWorker``: wire ``worker.progress`` to
``set_state``, ``worker.log`` to ``append_log``, ``worker.failed`` to
``finish_error``, and call ``finish_ok`` from your success handler.
"""

from PyQt5.QtCore import Qt, QTimer, QRectF
from PyQt5.QtGui import QColor, QPainter, QPen, QFont
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QToolButton, QPlainTextEdit,
    QSizePolicy,
)

# Shared palette (loosely borrowed from PlutoSpace's remote-session states).
ACCENT = "#5e7be1"   # busy / working
OK = "#2ecc71"       # ready
ERROR = "#e74c3c"    # failed
MUTED = "#888888"    # idle / detail text


class Spinner(QWidget):
    """A small arc that rotates on a timer. Hidden when stopped."""

    def __init__(self, parent=None, size=18, line_width=3):
        super().__init__(parent)
        self._angle = 0
        self._lw = line_width
        self._color = QColor(ACCENT)
        self.setFixedSize(size, size)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def start(self):
        if not self._timer.isActive():
            self._timer.start(80)
        self.show()

    def stop(self):
        self._timer.stop()
        self.hide()

    def set_color(self, color):
        self._color = QColor(color)
        self.update()

    def _tick(self):
        self._angle = (self._angle + 30) % 360
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self._lw, self._lw,
                      self.width() - 2 * self._lw, self.height() - 2 * self._lw)
        # faint full ring underneath
        track = QPen(QColor(self._color))
        track.setWidth(self._lw)
        c = QColor(self._color)
        c.setAlpha(60)
        track.setColor(c)
        p.setPen(track)
        p.drawArc(rect, 0, 360 * 16)
        # bright moving arc on top (Qt angles are 1/16 deg, 0 at 3 o'clock)
        head = QPen(QColor(self._color))
        head.setWidth(self._lw)
        head.setCapStyle(Qt.RoundCap)
        p.setPen(head)
        p.drawArc(rect, -self._angle * 16, 110 * 16)


class LiveStatusView(QWidget):
    """
    Drop-in status surface. Embed it wherever a slow op reports progress.

    Public API:
        start(state, detail="")        -> begin a run (spinner on, log shown if requested)
        set_state(state, detail="")    -> update the live line (connect to worker.progress)
        append_log(line)               -> add a log line   (connect to worker.log)
        finish_ok(detail="")           -> success terminal state (green check, spinner off)
        finish_error(message, hint="") -> failure terminal state (connect to worker.failed)
        reset()                        -> back to idle
    """

    def __init__(self, parent=None, log_visible=False):
        super().__init__(parent)
        self._build_ui()
        self._set_log_visible(log_visible)
        self.reset()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        row = QHBoxLayout()
        row.setSpacing(8)

        self.spinner = Spinner(self)
        row.addWidget(self.spinner, 0, Qt.AlignVCenter)

        # status icon shown when there's no spinner (✓ / ✕ / ·)
        self.icon = QLabel("")
        self.icon.setFixedWidth(18)
        self.icon.setAlignment(Qt.AlignCenter)
        f = QFont()
        f.setBold(True)
        self.icon.setFont(f)
        row.addWidget(self.icon, 0, Qt.AlignVCenter)

        text_col = QVBoxLayout()
        text_col.setSpacing(0)
        self.state_label = QLabel("")
        bold = QFont()
        bold.setBold(True)
        self.state_label.setFont(bold)
        self.detail_label = QLabel("")
        self.detail_label.setStyleSheet(f"color: {MUTED};")
        self.detail_label.setWordWrap(True)
        text_col.addWidget(self.state_label)
        text_col.addWidget(self.detail_label)
        row.addLayout(text_col, 1)

        self.log_toggle = QToolButton()
        self.log_toggle.setText("Details ▸")
        self.log_toggle.setCheckable(True)
        self.log_toggle.setAutoRaise(True)
        self.log_toggle.toggled.connect(self._on_toggle_log)
        row.addWidget(self.log_toggle, 0, Qt.AlignTop)

        outer.addLayout(row)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)  # ring buffer -- never grows unbounded
        self.log.setMinimumHeight(120)
        self.log.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        mono = QFont("Menlo")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSize(10)
        self.log.setFont(mono)
        outer.addWidget(self.log)

    # --- log pane visibility ---------------------------------------------------

    def _set_log_visible(self, visible):
        self.log.setVisible(visible)
        self.log_toggle.setChecked(visible)
        self.log_toggle.setText("Details ▾" if visible else "Details ▸")

    def _on_toggle_log(self, checked):
        self.log.setVisible(checked)
        self.log_toggle.setText("Details ▾" if checked else "Details ▸")

    # --- public API ------------------------------------------------------------

    def start(self, state="Working…", detail="", show_log=False):
        self.log.clear()
        self.icon.setText("")
        self.spinner.set_color(ACCENT)
        self.spinner.start()
        self.state_label.setStyleSheet(f"color: {ACCENT};")
        self.state_label.setText(self._pretty(state))
        self.detail_label.setText(detail)
        if show_log:
            self._set_log_visible(True)
        if detail:
            self.append_log(f"{self._pretty(state)} — {detail}")

    def set_state(self, state, detail=""):
        # Connected to SSHWorker.progress(state, detail).
        self.spinner.start()
        self.icon.setText("")
        self.state_label.setStyleSheet(f"color: {ACCENT};")
        self.state_label.setText(self._pretty(state))
        self.detail_label.setText(detail)
        self.append_log(f"{self._pretty(state)}{(' — ' + detail) if detail else ''}")

    def append_log(self, line):
        # Connected to SSHWorker.log(line).
        if line:
            self.log.appendPlainText(line)

    def finish_ok(self, detail=""):
        self.spinner.stop()
        self.icon.setStyleSheet(f"color: {OK};")
        self.icon.setText("✓")
        self.state_label.setStyleSheet(f"color: {OK};")
        self.state_label.setText("Done")
        self.detail_label.setText(detail)
        if detail:
            self.append_log(f"✓ {detail}")

    def finish_error(self, message, hint=""):
        # Connected to SSHWorker.failed(message, hint).
        self.spinner.stop()
        self.icon.setStyleSheet(f"color: {ERROR};")
        self.icon.setText("✕")
        self.state_label.setStyleSheet(f"color: {ERROR};")
        self.state_label.setText("Failed")
        self.detail_label.setText(hint or message)
        self.append_log(f"✕ {message}")
        if hint:
            self.append_log(f"  → {hint}")
        # Surface the log automatically on failure so the cause isn't buried.
        self._set_log_visible(True)

    def reset(self):
        self.spinner.stop()
        self.icon.setText("·")
        self.icon.setStyleSheet(f"color: {MUTED};")
        self.state_label.setStyleSheet(f"color: {MUTED};")
        self.state_label.setText("Idle")
        self.detail_label.setText("")
        self.log.clear()

    @staticmethod
    def _pretty(state):
        # "tunneling" -> "Tunneling"; leave the rest of the phrase as written.
        s = str(state)
        return s[:1].upper() + s[1:] if s else s
