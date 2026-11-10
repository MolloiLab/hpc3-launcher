#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Small building blocks shared by every page: cards, page headers, pills, stat
tiles, a segmented control, a segmented bar, and the table delegates that draw
pills and bars inside cells. All colours come from ``ui.theme``.
"""

from PyQt5.QtCore import Qt, QRectF, QSize, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPainterPath
from PyQt5.QtWidgets import (
    QWidget, QFrame, QLabel, QVBoxLayout, QHBoxLayout, QPushButton, QButtonGroup,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QStyledItemDelegate, QStyle, QSizePolicy, QInputDialog,
)

from ui import theme

SORT_ROLE = Qt.UserRole + 1     # numeric sort key for a cell
DATA_ROLE = Qt.UserRole + 2     # what a delegate draws (fraction, segments...)


# --- text + containers ---------------------------------------------------------

class WrapLabel(QLabel):
    """A word-wrapped label that always claims the height its text needs.

    A plain wrapped QLabel reports a minimum height for some nominal width, so a
    tight layout squeezes it and the last lines are clipped. This one re-derives
    its minimum height from its actual width whenever either changes.
    """

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        # Fixed vertically: exactly as tall as its text, so it neither gets
        # squeezed nor soaks up space freed when a sibling is hidden.
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

    def _fit(self):
        width = self.width()
        if width <= 0:
            return
        # QLabel never reports less than its current minimum height, so the old
        # floor has to go before measuring or the label could only ever grow.
        self.setMinimumHeight(0)
        needed = self.heightForWidth(width)
        if needed > 0:
            self.setFixedHeight(needed)
            self.updateGeometry()

    def sizeHint(self):
        hint = super().sizeHint()
        if self.width() > 0:
            hint.setHeight(self.heightForWidth(self.width()))
        return hint

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def setText(self, text):
        super().setText(text)
        self._fit()
        # Again once the layout has settled: a sibling shown or hidden in the same
        # breath changes this label's width only on the next pass.
        QTimer.singleShot(0, self._fit)


def label(text="", role=None, tone=None, wrap=False):
    """A QLabel with a theme role ("title", "muted", "section", ...)."""
    lab = WrapLabel(text) if wrap else QLabel(text)
    if role:
        lab.setProperty("role", role)
    if tone:
        lab.setProperty("tone", tone)
    return lab


def set_tone(lab, tone):
    lab.setProperty("tone", tone or "")
    theme.restyle(lab)


def dot(tone=None):
    """A small round status light. ``tone`` is a theme key (ok / warn / danger /
    accent / info / gpu_paid...) or None for neutral grey."""
    light = QLabel()
    light.setProperty("dot", tone or "none")
    light.setFixedSize(8, 8)
    return light


def set_dot(light, tone):
    light.setProperty("dot", tone or "none")
    theme.restyle(light)


def button(text, variant=None, on_click=None):
    """A QPushButton: variant is None (secondary), "primary", "danger", "ghost"
    (quiet, grey) or "link" (quiet, accent)."""
    btn = QPushButton(text)
    if variant:
        btn.setProperty("variant", variant)
    btn.setCursor(Qt.PointingHandCursor)
    if on_click:
        btn.clicked.connect(on_click)
    return btn


def ask_flag_note(parent, node):
    """Confirm flagging ``node`` as bad and ask why.

    Returns the note (possibly empty), or None if the user backed out.
    """
    note, ok = QInputDialog.getText(
        parent, "Flag node as bad",
        f"Future launches will be kept off {node} until you remove the flag.\n\n"
        "What went wrong? (optional)")
    return note.strip() if ok else None


def divider():
    line = QFrame()
    line.setProperty("divider", True)
    line.setFixedHeight(1)
    return line


class Card(QFrame):
    """A rounded surface with an optional title row. Add content to ``.body``."""

    def __init__(self, title=None, subtitle=None, parent=None, kind=True):
        super().__init__(parent)
        self.setProperty("card", kind)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(12)
        self.header = QHBoxLayout()
        self.header.setSpacing(8)
        if title:
            col = QVBoxLayout()
            col.setSpacing(2)
            self.title_label = label(title, "section")
            col.addWidget(self.title_label)
            if subtitle:
                self.subtitle_label = label(subtitle, "muted", wrap=True)
                col.addWidget(self.subtitle_label)
            self.header.addLayout(col, 1)
            outer.addLayout(self.header)
        self.body = QVBoxLayout()
        self.body.setSpacing(10)
        outer.addLayout(self.body, 1)

    def add_action(self, widget):
        """Put a widget (usually a button) at the right of the title row."""
        self.header.addWidget(widget, 0, Qt.AlignTop)


class PageHeader(QWidget):
    """Title + one-line subtitle on the left, action buttons on the right."""

    def __init__(self, title, subtitle="", parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.title_label = label(title, "title")
        self.subtitle_label = label(subtitle, "subtitle", wrap=True)
        col.addWidget(self.title_label)
        col.addWidget(self.subtitle_label)
        row.addLayout(col, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        row.addLayout(self.actions)

    def add_action(self, widget):
        self.actions.addWidget(widget, 0, Qt.AlignVCenter)

    def set_subtitle(self, text):
        self.subtitle_label.setText(text)


def page_layout(widget):
    """The standard page frame: generous margins, 16 px between blocks."""
    widget.setObjectName("page")
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(28, 24, 28, 18)
    layout.setSpacing(16)
    return layout


class Pill(QLabel):
    """A small rounded status tag."""

    def __init__(self, text="", tone="neutral", parent=None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.set_tone(tone)

    def set_tone(self, tone):
        set_tone(self, tone)       # a property, so it follows a light/dark switch

    def set(self, text, tone="neutral"):
        self.setText(text)
        self.set_tone(tone)


class StatTile(QFrame):
    """A headline number with a caption above and a detail line below."""

    def __init__(self, caption, value="—", detail="", parent=None):
        super().__init__(parent)
        self.setProperty("card", True)
        col = QVBoxLayout(self)
        col.setContentsMargins(14, 12, 14, 12)
        col.setSpacing(3)
        self.caption = label(caption, "field")
        self.value = label(value, "stat")
        self.detail = label(detail, "muted")
        col.addWidget(self.caption)
        col.addWidget(self.value)
        col.addWidget(self.detail)
        self.extra = QVBoxLayout()
        self.extra.setSpacing(6)
        col.addLayout(self.extra)
        col.addStretch(1)

    def set(self, value, detail=None, tone=None):
        self.value.setText(str(value))
        if detail is not None:
            self.detail.setText(detail)
            self.detail.setToolTip(detail)
        set_tone(self.value, tone)


class SegmentedControl(QFrame):
    """Two or more mutually exclusive choices drawn as one pill-shaped switch."""

    changed = pyqtSignal(object)

    def __init__(self, options, parent=None):
        """``options`` is [(label, data)]; the first is selected."""
        super().__init__(parent)
        self.setObjectName("segmented")
        row = QHBoxLayout(self)
        row.setContentsMargins(3, 3, 3, 3)
        row.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._data = []
        for index, (text, data) in enumerate(options):
            btn = QPushButton(text)
            btn.setProperty("segment", True)
            btn.setCheckable(True)
            self._group.addButton(btn, index)
            self._data.append(data)
            row.addWidget(btn, 1)
        self._group.button(0).setChecked(True)
        self._group.buttonClicked.connect(lambda _b: self.changed.emit(self.currentData()))

    def currentData(self):
        return self._data[max(0, self._group.checkedId())]

    def setCurrentData(self, data):
        if data in self._data:
            self._group.button(self._data.index(data)).setChecked(True)

    def setToolTips(self, tips):
        for index, tip in enumerate(tips):
            self._group.button(index).setToolTip(tip)


# --- bars ----------------------------------------------------------------------

def paint_segments(painter, rect, segments, radius=None):
    """Fill ``rect`` with proportional coloured segments, clipped to a rounded bar.

    ``segments`` is [(value, theme colour key)]. Zero-valued segments are skipped;
    an all-zero bar shows just the track.
    """
    radius = rect.height() / 2 if radius is None else radius
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(rect), radius, radius)
    painter.setClipPath(clip)
    painter.fillRect(rect, theme.color("track"))
    total = float(sum(max(0, v) for v, _ in segments))
    if total > 0:
        x = float(rect.x())
        live = [(v, key) for v, key in segments if v > 0]
        gap = 2.0 if len(live) > 1 else 0.0
        width = rect.width() - gap * (len(live) - 1)
        for index, (value, key) in enumerate(live):
            w = width * value / total
            painter.fillRect(QRectF(x, rect.y(), w, rect.height()), theme.color(key))
            x += w + gap
    painter.restore()


class SegmentBar(QWidget):
    """A standalone proportional bar (paid / free / idle, used / available...)."""

    def __init__(self, parent=None, height=5):
        super().__init__(parent)
        self._segments = []
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_segments(self, segments):
        self._segments = list(segments)
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        paint_segments(painter, self.rect(), self._segments)


class Legend(QWidget):
    """Coloured dots with labels, for whatever a SegmentBar's colours mean."""

    def __init__(self, entries, parent=None):
        """``entries`` is [(theme colour key, text)]."""
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(14)
        for key, text in entries:
            item = QHBoxLayout()
            item.setSpacing(6)
            item.addWidget(dot(key), 0, Qt.AlignVCenter)
            item.addWidget(label(text, "faint"))
            row.addLayout(item)
        row.addStretch(1)


# --- tables --------------------------------------------------------------------

class SortItem(QTableWidgetItem):
    """A cell that sorts by a numeric key when one is set, else by its text."""

    def __init__(self, text="", sort_key=None):
        super().__init__(str(text))
        if sort_key is not None:
            self.setData(SORT_ROLE, sort_key)

    def __lt__(self, other):
        mine, theirs = self.data(SORT_ROLE), other.data(SORT_ROLE)
        if mine is not None and theirs is not None:
            try:
                return mine < theirs
            except TypeError:
                pass
        return super().__lt__(other)


def make_table(headers, stretch=None, sortable=True):
    """A QTableWidget configured the way every page wants it.

    ``stretch`` is the column that absorbs spare width (default: the last).
    """
    table = QTableWidget()
    table.setColumnCount(len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(38)
    table.setShowGrid(False)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setFocusPolicy(Qt.NoFocus)
    table.setWordWrap(False)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
    table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
    header = table.horizontalHeader()
    header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    header.setHighlightSections(False)
    header.setSectionResizeMode(QHeaderView.Interactive)
    header.setMinimumSectionSize(60)
    stretch = len(headers) - 1 if stretch is None else stretch
    header.setSectionResizeMode(stretch, QHeaderView.Stretch)
    table.setSortingEnabled(sortable)
    if sortable:        # clicking a header sorts, so it should look clickable
        header.viewport().setCursor(Qt.PointingHandCursor)
    return table


def _base_paint(delegate, painter, option, index):
    """Draw the cell's background/selection exactly as the style would, no text."""
    opt = option.__class__(option)
    delegate.initStyleOption(opt, index)
    opt.text = ""
    style = opt.widget.style() if opt.widget else None
    if style:
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, opt.widget)


def _sentence_case(text):
    """RUNNING -> Running; anything already mixed-case (free-gpu32) is left alone."""
    return text.capitalize() if text.isupper() else text


class PillDelegate(QStyledItemDelegate):
    """Draws a cell as a small coloured dot and plain text, toned by
    ``tone_for(text)``. (It used to draw a filled pill; a table full of those was
    louder than the data in it.)"""

    def __init__(self, tone_for=None, parent=None):
        super().__init__(parent)
        self._tone_for = tone_for or theme.state_tone

    def paint(self, painter, option, index):
        _base_paint(self, painter, option, index)
        text = str(index.data(Qt.DisplayRole) or "")
        if not text:
            return
        fg, _bg = theme.tone_colors(self._tone_for(text))
        text = _sentence_case(text)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = option.rect.adjusted(10, 0, -8, 0)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(fg))
        painter.drawEllipse(QRectF(rect.x(), rect.center().y() - 3, 7, 7))
        painter.setPen(theme.color("text"))
        painter.setFont(option.font)
        text_rect = QRectF(rect.x() + 14, rect.y(), max(0, rect.width() - 14), rect.height())
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft,
                         painter.fontMetrics().elidedText(text, Qt.ElideRight,
                                                          int(text_rect.width())))
        painter.restore()


class BarDelegate(QStyledItemDelegate):
    """Text on the left, a thin bar on the right.

    The cell's ``DATA_ROLE`` holds the bar: either a fraction (0..1), drawn in a
    colour that warms as it fills, or a list of ``(value, colour key)`` segments.
    """

    BAR_WIDTH = 84

    def paint(self, painter, option, index):
        _base_paint(self, painter, option, index)
        data = index.data(DATA_ROLE)
        text = str(index.data(Qt.DisplayRole) or "")
        rect = option.rect.adjusted(10, 0, -12, 0)
        bar_w = min(self.BAR_WIDTH, max(40, rect.width() // 2)) if data is not None else 0
        painter.save()
        painter.setPen(theme.color("text"))
        painter.setFont(option.font)
        text_rect = QRectF(rect.x(), rect.y(), rect.width() - bar_w - 10, rect.height())
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft,
                         painter.fontMetrics().elidedText(text, Qt.ElideRight,
                                                          int(text_rect.width())))
        painter.restore()
        if data is None:
            return
        bar = QRectF(rect.right() - bar_w, rect.center().y() - 2, bar_w, 5)
        if isinstance(data, (int, float)):
            frac = max(0.0, min(1.0, float(data)))
            key = "danger" if frac > 0.9 else "warn" if frac > 0.7 else "accent"
            segments = [(frac, key), (1.0 - frac, "track")]
        else:
            segments = data
        paint_segments(painter, bar, segments)

    def sizeHint(self, option, index):
        hint = super().sizeHint(option, index)
        return QSize(hint.width() + self.BAR_WIDTH + 10, hint.height())
