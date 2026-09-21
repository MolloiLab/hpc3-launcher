#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from PyQt5.QtWidgets import QComboBox, QStyle, QStyleOptionComboBox, QStylePainter
from PyQt5.QtCore import Qt, QEvent, pyqtSignal
from PyQt5.QtGui import QStandardItem, QStandardItemModel


class CheckableComboBox(QComboBox):
    """A dropdown whose rows are checkboxes, so several can be picked at once.

    It paints like every other (non-editable) combo on the form; only the label
    differs -- a summary of what's checked instead of a "current item". Clicking a
    row toggles it and leaves the popup open, so ticking three nodes is three clicks.
    """

    checked_changed = pyqtSignal()

    def __init__(self, parent=None, empty_text="None"):
        super().__init__(parent)
        self._empty_text = empty_text
        self.setModel(QStandardItemModel(self))
        self.view().viewport().installEventFilter(self)

    def set_items(self, items, checked=()):
        """Replace the rows. ``items`` is [(label, value)]; ``checked`` the ticked values."""
        checked = set(checked)
        model = self.model()
        model.clear()
        for label, value in items:
            item = QStandardItem(label)
            item.setData(value, Qt.UserRole)
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            item.setData(Qt.Checked if value in checked else Qt.Unchecked, Qt.CheckStateRole)
            model.appendRow(item)
        self.update()

    def checked_values(self):
        model = self.model()
        return [model.item(i).data(Qt.UserRole) for i in range(model.rowCount())
                if model.item(i).checkState() == Qt.Checked]

    def eventFilter(self, obj, event):
        if obj is self.view().viewport() and event.type() == QEvent.MouseButtonRelease:
            item = self.model().itemFromIndex(self.view().indexAt(event.pos()))
            if item is not None:
                item.setCheckState(Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)
                self.update()
                self.checked_changed.emit()
            return True  # swallow the release: that's what would close the popup
        return super().eventFilter(obj, event)

    def paintEvent(self, event):
        values = self.checked_values()
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        if not values:
            option.currentText = self._empty_text
        elif len(values) <= 2:
            option.currentText = ", ".join(str(v) for v in values)
        else:  # a longer list would just be clipped
            option.currentText = f"{len(values)} selected"
        painter = QStylePainter(self)
        painter.drawComplexControl(QStyle.CC_ComboBox, option)
        painter.drawControl(QStyle.CE_ComboBoxLabel, option)
