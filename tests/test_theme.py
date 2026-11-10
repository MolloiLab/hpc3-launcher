#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the theme: light/dark switching and pointing-hand cursors.

Both are easy to break without noticing -- a colour cached at build time survives
a theme switch looking wrong, and a new control added without a cursor just feels
dead -- so they are pinned here. The settings file is a temp file; the real one is
never read or written.
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "hpc3_launcher"))

from PyQt5.QtCore import Qt, QEvent, QSettings  # noqa: E402
from PyQt5.QtWidgets import (  # noqa: E402
    QApplication, QCheckBox, QComboBox, QLabel, QPushButton, QTabBar, QVBoxLayout, QWidget,
)

from modules import cluster_view as cv  # noqa: E402
from ui import theme  # noqa: E402
from ui.components import SegmentedControl, button, dot, make_table, set_dot  # noqa: E402
from ui.node_status_widget import NodeStatusWidget  # noqa: E402


class ThemeTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv[:1])
        # Other test modules leave whole pages behind in this process; restyling
        # all of them on every switch below would take a minute.
        for widget in cls.app.topLevelWidgets():
            widget.deleteLater()
        cls.app.sendPostedEvents(None, QEvent.DeferredDelete)
        cls.app.processEvents()

    def setUp(self):
        ini = os.path.join(tempfile.mkdtemp(prefix="hpc3-theme-test-"), "s.ini")
        for patcher in (
                mock.patch.object(theme, "_settings",
                                  lambda: QSettings(ini, QSettings.IniFormat)),
                mock.patch.dict(os.environ)):
            patcher.start()
            self.addCleanup(patcher.stop)
        os.environ.pop("HPC3_THEME", None)
        theme.apply_theme(self.app, "light")
        self.addCleanup(lambda: theme.apply_theme(self.app, "light"))

    def show(self, widget):
        widget.show()
        self.addCleanup(widget.deleteLater)
        self.app.processEvents()
        return widget


class TestModes(ThemeTestCase):
    def test_switching_changes_the_palette_and_is_remembered(self):
        self.assertEqual(theme.saved_mode(), "system")
        seen = []
        theme.notifier.changed.connect(lambda: seen.append(theme.colors()["name"]))
        theme.set_mode("dark", self.app)
        self.assertEqual(theme.colors()["name"], "dark")
        self.assertEqual(theme.saved_mode(), "dark")
        self.assertIn(theme.DARK["bg"], self.app.styleSheet())
        theme.set_mode("light", self.app)
        self.assertEqual(theme.colors()["name"], "light")
        self.assertNotIn(theme.DARK["bg"], self.app.styleSheet())
        self.assertEqual(seen, ["dark", "light"])

    def test_system_follows_the_os(self):
        for os_dark, name in ((True, "dark"), (False, "light")):
            with mock.patch.object(theme, "_os_prefers_dark", return_value=os_dark):
                theme.set_mode("system", self.app)
                self.assertEqual(theme.colors()["name"], name)
        self.assertEqual(theme.saved_mode(), "system")

    def test_the_environment_override_wins(self):
        os.environ["HPC3_THEME"] = "dark"
        theme.set_mode("light", self.app)
        self.assertEqual(theme.colors()["name"], "dark")
        self.assertEqual(theme.current_mode(), "dark")

    def test_an_unknown_mode_is_refused_and_a_bad_setting_is_ignored(self):
        with self.assertRaises(ValueError):
            theme.set_mode("sepia", self.app)
        theme._settings().setValue(theme.SETTINGS_KEY, "sepia")
        self.assertEqual(theme.saved_mode(), "system")

    def test_both_palettes_define_the_same_colours(self):
        self.assertEqual(set(theme.LIGHT), set(theme.DARK))
        for palette in (theme.LIGHT, theme.DARK):
            sheet = theme.build_stylesheet(palette, theme._build_assets(palette))
            self.assertNotIn("\\", sheet)       # a Windows temp path must not leak in
            self.assertNotIn("None", sheet)

    def test_a_monospace_font_this_machine_has(self):
        font = theme.mono_font(12)
        self.assertTrue(font.family())
        self.assertEqual(font.pixelSize(), 12)


class TestNothingKeepsAStaleColour(ThemeTestCase):
    def test_status_lights_follow_the_theme(self):
        light = dot("ok")
        self.show(light)
        self.assertEqual(light.property("dot"), "ok")
        set_dot(light, None)
        self.assertEqual(light.property("dot"), "none")
        self.assertEqual(light.styleSheet(), "")    # styled by the app sheet, not its own

    def test_the_nodes_table_is_redrawn_in_the_new_colours(self):
        with open(os.path.join(HERE, "fixtures", "cluster_snapshot.txt"), encoding="utf-8") as fh:
            snapshot = cv.build_snapshot(fh.read(), "demo")
        flags = mock.MagicMock()
        flags.is_flagged.return_value = False
        page = NodeStatusWidget(username="demo", insights=None, flags=flags)
        self.addCleanup(page.deleteLater)
        page.update_snapshot(snapshot)

        def room_colours():
            table = page.gpu_nodes_table
            return {table.item(r, 3).foreground().color().name().upper()
                    for r in range(table.rowCount()) if table.item(r, 3).text() != "—"}

        self.assertIn(theme.LIGHT["ok"], room_colours())
        theme.set_mode("dark", self.app)
        self.assertIn(theme.DARK["ok"], room_colours())
        self.assertNotIn(theme.LIGHT["ok"], room_colours())


class TestClickableThingsPoint(ThemeTestCase):
    def cursor(self, widget):
        return widget.cursor().shape()

    def test_every_kind_of_control(self):
        host = QWidget()
        box = QVBoxLayout(host)
        plain = QPushButton("made without the helper")
        helper = button("made with it", "ghost")
        combo, check, tabs = QComboBox(), QCheckBox("tick"), QTabBar()
        tabs.addTab("one")
        switch = SegmentedControl([("A", 1), ("B", 2)])
        text = QLabel("just text")
        for w in (plain, helper, combo, check, tabs, switch, text):
            box.addWidget(w)
        self.show(host)
        for w in (plain, helper, combo, check, tabs):
            with self.subTest(widget=type(w).__name__):
                self.assertEqual(self.cursor(w), Qt.PointingHandCursor)
        for segment in switch.findChildren(QPushButton):
            self.assertEqual(self.cursor(segment), Qt.PointingHandCursor)
        self.assertEqual(self.cursor(text), Qt.ArrowCursor)

    def test_a_disabled_control_does_not_invite_a_click(self):
        btn = QPushButton("go")
        self.show(btn)
        self.assertEqual(self.cursor(btn), Qt.PointingHandCursor)
        btn.setEnabled(False)
        self.app.processEvents()
        self.assertEqual(self.cursor(btn), Qt.ArrowCursor)
        btn.setEnabled(True)
        self.app.processEvents()
        self.assertEqual(self.cursor(btn), Qt.PointingHandCursor)

    def test_rows_point_only_where_clicking_a_row_does_something(self):
        clickable = make_table(["A", "B"], sortable=False)
        plain = make_table(["A", "B"])
        clickable.setProperty("clickableRows", True)
        host = QWidget()
        box = QVBoxLayout(host)
        box.addWidget(clickable)
        box.addWidget(plain)
        self.show(host)
        self.assertEqual(clickable.viewport().cursor().shape(), Qt.PointingHandCursor)
        self.assertEqual(plain.viewport().cursor().shape(), Qt.ArrowCursor)
        # A header points only where clicking it sorts.
        self.assertEqual(plain.horizontalHeader().viewport().cursor().shape(),
                         Qt.PointingHandCursor)
        self.assertEqual(clickable.horizontalHeader().viewport().cursor().shape(),
                         Qt.ArrowCursor)


if __name__ == "__main__":
    unittest.main()
