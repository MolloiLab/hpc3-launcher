#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the Sessions page's launch form, driven as a user would drive it.

The form is a cascade -- tier, account, GPU, GPU count, memory, CPUs, run time --
and its whole job is that no sequence of picks can describe a request HPC3 would
reject. These tests build the real widget (Qt's offscreen platform, no network)
and click through it.
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "hpc3_launcher"))

from PyQt5.QtCore import Qt, QSettings  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from modules import cluster_view as cv  # noqa: E402
from modules import hpc3_constraints as hc  # noqa: E402
from modules.node_flags import NodeFlagStore  # noqa: E402
from ui import theme  # noqa: E402
from ui.vscode_widget import VSCodeWidget, decode_choice, encode_choice  # noqa: E402

# In capitals, exactly as ``sbank`` hands them over -- while every Slurm command
# (and the fixture's fair-share table) spells them in lower case. Version 0.5.0
# was tested with lower-case names here and shipped unable to match the two.
ACCOUNTS = [
    {"name": "DEMO", "is_personal": True, "available": 199820},
    {"name": "PI_LAB", "is_personal": False, "available": 1204300},
    {"name": "PI_LAB_GPU", "is_personal": False, "available": 84000},
    {"name": "PI_LAB_GPU32", "is_personal": False, "available": 2310775},
]
BAD_NODE = "hpc3-gpu-n54-00"


def load_snapshot():
    with open(os.path.join(HERE, "fixtures", "cluster_snapshot.txt"), encoding="utf-8") as fh:
        return cv.build_snapshot(fh.read(), "demo")


class FormTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv[:1])
        theme.apply_theme(cls.app)
        cls.tmp = tempfile.mkdtemp(prefix="hpc3-form-test-")

    def setUp(self):
        # A private settings file per test: never read or write the real one.
        ini = os.path.join(self.tmp, f"{self.id()}.ini")
        patcher = mock.patch.object(VSCodeWidget, "_settings",
                                    lambda _self: QSettings(ini, QSettings.IniFormat))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.form = self.make_form()

    def make_form(self, accounts=ACCOUNTS, snapshot="fixture"):
        form = VSCodeWidget(username=None)      # no username -> no managers, no SSH
        form.initialization_complete = True
        form.vscode_manager = mock.Mock()
        form._on_options_loaded({
            "accounts": list(accounts), "running": [], "nodes": {},
            "snapshot": load_snapshot() if snapshot == "fixture" else snapshot})
        form._sync_timer.stop()
        self.addCleanup(form.deleteLater)
        return form

    # -- helpers that act like a user --
    def pick_gpu(self, family, gpu_type, form=None):
        form = form or self.form
        index = form.gpu_combo.findData(encode_choice(family, gpu_type))
        self.assertGreaterEqual(index, 0, f"{family}/{gpu_type} is not offered")
        form.gpu_combo.setCurrentIndex(index)

    def pick_account(self, name, form=None):
        form = form or self.form
        index = form.account_combo.findData(name)
        self.assertGreaterEqual(index, 0)
        form.account_combo.setCurrentIndex(index)
        form._on_account_picked(index)          # what a real click also fires

    def set_tier(self, free, form=None):
        form = form or self.form
        form.free_option_check.setCurrentData(free)
        form.on_free_changed()

    def offered(self, form=None):
        form = form or self.form
        return [decode_choice(form.gpu_combo.itemData(i))
                for i in range(form.gpu_combo.count())]


class TestOpensOnTheBestFreeRequest(FormTestCase):
    def test_free_is_the_default_tier(self):
        self.assertTrue(self.form.free_option_check.currentData())
        self.assertTrue(self.form._request()["use_free"])

    def test_the_best_priority_account_is_chosen(self):
        self.assertEqual(self.form.account_combo.currentData(), "pi_lab_gpu")
        self.assertIn("best for free", self.form.account_combo.currentText())
        # Listed best first.
        order = [self.form.account_combo.itemData(i)
                 for i in range(self.form.account_combo.count())]
        self.assertEqual(order, ["pi_lab_gpu", "pi_lab", "demo", "pi_lab_gpu32"])

    def test_a_gpu32_lab_opens_on_gpu32(self):
        req = self.form._request()
        self.assertEqual((req["family"], req["partition"]), ("gpu32", "free-gpu32"))

    def test_a_cpu_only_user_opens_on_no_gpu(self):
        form = self.make_form(accounts=ACCOUNTS[:2])
        req = form._request()
        self.assertEqual((req["gpu_type"], req["partition"]), (None, "free"))
        self.assertFalse(form.gpu_count_spinbox.isEnabled())

    def test_works_without_a_snapshot(self):
        form = self.make_form(snapshot=None)
        self.assertIsNotNone(form._request())
        self.assertTrue(form.advice_card.isHidden())
        self.assertTrue(form.submit_btn.isEnabled())


class TestNoInvalidCombination(FormTestCase):
    def test_every_free_pick_resolves_to_a_legal_partition(self):
        for account in [a["name"].lower() for a in ACCOUNTS]:
            self.pick_account(account)
            for family, gpu_type in self.offered():
                self.pick_gpu(family, gpu_type)
                req = self.form._request()
                with self.subTest(account=account, family=family, gpu=gpu_type):
                    self.assertEqual(req["account"], account)
                    if gpu_type in hc.GPU_MODELS:
                        own = hc.GPU_MODELS[gpu_type]["family"]
                        self.assertEqual(req["partition"], hc.FAMILIES[own]["free"])
                    self.assertTrue(all(p.startswith("free")
                                        for p in req["partition"].split(",")))

    def test_paid_offers_only_the_accounts_own_family(self):
        self.set_tier(False)
        self.pick_account("pi_lab_gpu")
        self.assertEqual([g for _f, g in self.offered() if g], ["V100", "A30", "A100"])
        self.pick_account("pi_lab_gpu32")
        self.assertEqual([g for _f, g in self.offered() if g], ["L40S", "RTX6000"])
        self.pick_account("demo")
        self.assertEqual(self.offered(), [("cpu", None)])

    def test_every_paid_pick_matches_the_account(self):
        self.set_tier(False)
        expected = {"demo": "standard", "pi_lab": "standard",
                    "pi_lab_gpu": "gpu", "pi_lab_gpu32": "gpu32"}
        for account, partition in expected.items():
            self.pick_account(account)
            for family, gpu_type in self.offered():
                self.pick_gpu(family, gpu_type)
                with self.subTest(account=account, gpu=gpu_type):
                    self.assertEqual(self.form._request()["partition"], partition)

    def test_switching_to_paid_drops_a_gpu_the_account_cannot_buy(self):
        # Free: RTX6000 under the pi_lab_gpu account is fine...
        self.pick_account("pi_lab_gpu")
        self.pick_gpu("gpu32", "RTX6000")
        self.assertEqual(self.form._request()["partition"], "free-gpu32")
        # ...paid: that account can't reach gpu32, so the pick must not survive.
        self.set_tier(False)
        self.pick_account("pi_lab_gpu")
        req = self.form._request()
        self.assertNotEqual(req["gpu_type"], "RTX6000")
        self.assertEqual(req["partition"], "gpu")

    def test_a100_and_gpu32_can_never_meet(self):
        for free in (True, False):
            self.set_tier(free)
            for account in [a["name"].lower() for a in ACCOUNTS]:
                self.pick_account(account)
                for family, gpu_type in self.offered():
                    self.pick_gpu(family, gpu_type)
                    partition = self.form._request()["partition"]
                    with self.subTest(free=free, account=account, gpu=gpu_type):
                        if gpu_type in ("V100", "A30", "A100"):
                            self.assertNotIn("gpu32", partition)
                        if gpu_type in ("L40S", "RTX6000"):
                            self.assertTrue(partition.endswith("gpu32"))


class TestDerivedLimits(FormTestCase):
    def test_memory_sets_the_cores_and_they_cannot_go_lower(self):
        self.pick_gpu("gpu32", "RTX6000")
        self.form.gpu_count_spinbox.setValue(2)
        self.form.memory_combo.setCurrentText("128G")
        self.assertEqual(self.form.cpu_spinbox.value(), 22)      # 128 GB / 6 GB per core
        self.form.cpu_spinbox.setValue(4)
        self.assertEqual(self.form.cpu_spinbox.value(), 22)
        self.pick_gpu("gpu", "A30")                              # 9 GB per core there
        self.assertEqual(self.form.cpu_spinbox.value(), 15)

    def test_gpu_count_is_capped_per_node(self):
        self.pick_gpu("gpu", "A100")
        self.assertEqual(self.form.gpu_count_spinbox.maximum(), 2)
        self.pick_gpu("gpu32", "RTX6000")
        self.assertEqual(self.form.gpu_count_spinbox.maximum(), 4)

    def test_free_run_time_stops_at_three_days(self):
        model = self.form.time_combo.model()
        enabled = {self.form.time_combo.itemText(i): model.item(i).isEnabled()
                   for i in range(self.form.time_combo.count())}
        self.assertTrue(enabled["3-00:00:00"])
        self.assertFalse(enabled["7-00:00:00"])
        self.set_tier(False)
        self.pick_account("pi_lab_gpu32")
        self.form.time_combo.setCurrentText("14-00:00:00")
        self.set_tier(True)                 # back to free: snapped down to the cap
        self.assertEqual(self.form.time_combo.currentText(), "3-00:00:00")

    def test_summary_shows_cost_only_when_paid(self):
        self.pick_gpu("gpu32", "RTX6000")
        self.assertIn("free", self.form.status_label.text())
        self.assertNotIn("SUs", self.form.status_label.text())
        self.set_tier(False)
        self.pick_account("pi_lab_gpu32")
        self.pick_gpu("gpu32", "RTX6000")
        self.assertIn("SUs", self.form.status_label.text())


class TestFreeTierAdvice(FormTestCase):
    def test_priority_is_shown_for_the_chosen_account(self):
        self.assertIn("2,110", self.form.priority_label.text())
        self.assertTrue(self.form.use_best_btn.isHidden())

    def test_a_worse_account_is_called_out_and_one_click_fixes_it(self):
        self.pick_account("pi_lab_gpu32")
        self.assertIn("383", self.form.priority_label.text())
        self.assertIn("pi_lab_gpu", self.form.priority_label.text())
        self.assertFalse(self.form.use_best_btn.isHidden())
        self.form.use_best_btn.click()
        self.assertEqual(self.form.account_combo.currentData(), "pi_lab_gpu")
        self.assertTrue(self.form.use_best_btn.isHidden())

    def test_an_account_picked_by_hand_is_left_alone(self):
        self.pick_account("pi_lab_gpu32")
        self.form._on_snapshot(load_snapshot())          # a background refresh
        self.assertEqual(self.form.account_combo.currentData(), "pi_lab_gpu32")

    def test_changing_tier_goes_back_to_the_best_account(self):
        self.pick_account("pi_lab_gpu32")
        self.set_tier(False)
        self.set_tier(True)
        self.assertEqual(self.form.account_combo.currentData(), "pi_lab_gpu")

    def test_oversized_request_offers_to_shrink_and_then_fits(self):
        self.pick_gpu("gpu32", "RTX6000")
        self.form.gpu_count_spinbox.setValue(1)
        self.form.memory_combo.setCurrentText("96G")
        self.assertIn("Too big", self.form.fit_label.text())
        self.assertFalse(self.form.shrink_btn.isHidden())
        self.form.shrink_btn.click()
        self.assertEqual(self.form.memory_combo.currentText(), "48G")
        self.assertIn("Fits right now", self.form.fit_label.text())
        self.assertTrue(self.form.shrink_btn.isHidden())
        req = self.form._request()
        snap = self.form.snapshot
        self.assertTrue(cv.request_fits(snap["nodes"]["hpc3-gpu-n54-00"], 1, req["cpus"],
                                        hc.parse_mem_gb(req["memory"])))

    def test_no_node_with_enough_idle_gpus_just_queues(self):
        self.pick_gpu("gpu32", "RTX6000")
        self.form.gpu_count_spinbox.setValue(2)
        self.assertIn("Will queue", self.form.fit_label.text())
        self.assertTrue(self.form.shrink_btn.isHidden())

    def test_advice_is_hidden_for_paid(self):
        self.set_tier(False)
        self.assertTrue(self.form.advice_card.isHidden())


class TestBadNodes(FormTestCase):
    def flag_store(self):
        return NodeFlagStore(self.form._settings)

    def test_a_node_other_users_avoid_is_pointed_out(self):
        self.pick_gpu("gpu32", "RTX6000")
        self.assertIn(BAD_NODE, self.form.suspect_label.text())
        self.assertIn("6 other users", self.form.suspect_label.text())
        self.assertFalse(self.form.avoid_suspect_btn.isHidden())
        # It is not in the pool of an A30 request, so it is not mentioned there.
        self.pick_gpu("gpu", "A30")
        self.assertTrue(self.form.suspect_label.isHidden())

    def test_one_click_flags_it_and_the_launch_stays_off_it(self):
        self.pick_gpu("gpu32", "RTX6000")
        self.form.memory_combo.setCurrentText("16G")
        self.assertIn(f"Fits right now</b> on {BAD_NODE}", self.form.fit_label.text())
        self.form.avoid_suspect_btn.click()
        self.assertEqual(self.form.flags.names(), [BAD_NODE])
        self.assertTrue(self.form.suspect_label.isHidden())
        self.assertEqual(self.form.exclude_combo.checked_values(), [BAD_NODE])
        self.assertIn(f"Kept off {BAD_NODE}", self.form.status_label.text())
        # The idle GPU on the flagged node no longer counts as somewhere to start.
        self.assertNotIn("Fits right now", self.form.fit_label.text())
        self.assertIn("node you flagged", self.form.fit_label.text())
        with mock.patch.object(self.form, "_do_submit"):
            self.form.submit_job()
        self.assertEqual(self.form._pending_submit["exclude_nodes"], [BAD_NODE])

    def test_a_flag_is_remembered_by_the_next_form(self):
        self.form.flags.flag(BAD_NODE, "GPU 2 dead")
        again = self.make_form()
        self.assertEqual(again.exclude_combo.checked_values(), [BAD_NODE])
        self.assertIn("GPU 2 dead", again.exclude_combo.model().item(0).text())
        self.assertEqual(self.flag_store().names(), [BAD_NODE])

    def test_ticking_the_picker_is_flagging(self):
        self.form.partition_nodes = {"free-gpu32": [(BAD_NODE, "mixed"),
                                                    ("hpc3-gpu-n54-01", "mixed")]}
        self.form._apply_cascade()
        model = self.form.exclude_combo.model()
        rows = {model.item(i).data(Qt.UserRole): model.item(i) for i in range(model.rowCount())}
        self.assertIn("6 other users avoid it", rows[BAD_NODE].text())
        self.assertEqual(rows["hpc3-gpu-n54-01"].text(), "hpc3-gpu-n54-01  — 2 other users avoid it")
        rows["hpc3-gpu-n54-01"].setCheckState(Qt.Checked)
        self.form.exclude_combo.checked_changed.emit()
        self.assertEqual(self.flag_store().names(), ["hpc3-gpu-n54-01"])
        model = self.form.exclude_combo.model()
        for i in range(model.rowCount()):
            model.item(i).setCheckState(Qt.Unchecked)
        self.form.exclude_combo.checked_changed.emit()
        self.assertEqual(self.flag_store().names(), [])
        self.assertNotIn("Kept off", self.form.status_label.text())

    def test_a_session_node_can_be_flagged_and_unflagged(self):
        fetch = mock.patch.object(self.form, "_fetch_config_for")   # no SSH in tests
        fetch.start()
        self.addCleanup(fetch.stop)
        self.form._apply_queue([{"job_id": "1", "status": "RUNNING", "node": BAD_NODE,
                                 "partition": "free-gpu32"},
                                {"job_id": "2", "status": "PENDING", "node": None,
                                 "partition": "free-gpu32"}])
        self.form.sessions_table.selectRow(1)             # the pending one: no node yet
        self.assertFalse(self.form.flag_btn.isEnabled())
        self.form.sessions_table.selectRow(0)
        self.assertTrue(self.form.flag_btn.isEnabled())
        with mock.patch("ui.vscode_widget.ask_flag_note", return_value=None):
            self.form.flag_btn.click()                    # backed out of the prompt
        self.assertEqual(self.form.flags.names(), [])
        with mock.patch("ui.vscode_widget.ask_flag_note", return_value="CUDA errors"):
            self.form.flag_btn.click()
        self.assertIn("CUDA errors", self.form.flags.describe(BAD_NODE))
        self.assertEqual(self.form.flag_btn.text(), "Remove flag")
        self.form.flag_btn.click()
        self.assertEqual(self.form.flags.names(), [])

    def test_a_flag_for_a_node_the_cluster_no_longer_has_is_not_sent(self):
        # sbatch rejects --exclude of an unknown node, which would fail every launch.
        self.form.flags.flag("hpc3-gpu-gone-99")
        self.form.flags.flag(BAD_NODE)
        self.assertEqual(self.form._avoided(), [BAD_NODE])
        self.assertEqual(self.form.flags.names(), ["hpc3-gpu-gone-99", BAD_NODE])


class TestCompareGpus(FormTestCase):
    REPLIES = {"L40S": "2026-11-10T13:02:27", "RTX6000": "2026-11-10T12:58:25",
               "V100": "2026-11-09T19:41:19", "A30": "2026-11-09T19:41:18", "A100": None}

    def run_comparison(self):
        """Press the button with Slurm faked; returns the commands it was sent."""
        sent = []

        class Insights:
            def estimate_starts(_self, commands):
                sent.extend(commands)
                out = []
                for command in commands:
                    model = command.split("--gres=gpu:")[1].split(":")[0]
                    start = self.REPLIES[model]
                    out.append({"ok": bool(start), "start": start or "", "node": "n1",
                                "message": "" if start else "node configuration unavailable"})
                return out

        self.form.insights = Insights()
        self.form._apply_cascade()
        self.form.compare_starts()
        self.form._compare_worker.wait(5000)
        self.app.processEvents()
        return sent

    def rows(self):
        return [(model, when, tile.isEnabled(), tile.isChecked())
                for _family, model, tile, when in self.form._compare_rows]

    def test_every_model_is_asked_about_the_same_size(self):
        self.form.flags.flag(BAD_NODE)
        self.pick_gpu("gpu32", "RTX6000")
        self.form.gpu_count_spinbox.setValue(4)
        self.form.memory_combo.setCurrentText("48G")
        sent = self.run_comparison()
        self.assertEqual(len(sent), 5)
        for command in sent:
            self.assertIn("sbatch --test-only ", command)
            self.assertIn(" --mem=48G ", command)
            self.assertIn(" --account=pi_lab_gpu ", command)
            self.assertIn(f" --exclude={BAD_NODE}", command)
        by_model = {c.split("--gres=gpu:")[1].split(" ")[0]: c for c in sent}
        # Each on its own partition, cores by that partition's rule, GPU count
        # clamped to what one node of that model has.
        self.assertIn(" -p free-gpu32 ", by_model["RTX6000:4"])
        self.assertIn(" --cpus-per-task=8 ", by_model["RTX6000:4"])
        self.assertIn(" -p free-gpu ", by_model["A100:2"])
        self.assertIn(" --cpus-per-task=6 ", by_model["A100:2"])    # 48 GB / 9 per core
        self.assertIn(" --cpus-per-task=8 ", by_model["A30:4"])     # 2 cores per GPU

    def test_soonest_first_and_one_click_switches(self):
        self.pick_gpu("gpu32", "RTX6000")
        self.run_comparison()
        self.assertFalse(self.form.compare_box.isHidden())
        rows = self.rows()
        self.assertEqual([r[0] for r in rows], ["A30", "V100", "RTX6000", "L40S", "A100"])
        self.assertEqual(rows[-1][1:], ("won't run", False, False))
        self.assertEqual([r[3] for r in rows], [False, False, True, False, False])
        self.form._compare_rows[0][2].click()                # the A30 tile
        req = self.form._request()
        self.assertEqual((req["gpu_type"], req["partition"]), ("A30", "free-gpu"))
        self.assertFalse(self.form.compare_box.isHidden())   # same size: still valid
        self.assertEqual([r[3] for r in self.rows()], [True, False, False, False, False])
        self.form._compare_rows[0][2].click()                # clicking it again changes nothing
        self.assertEqual(self.form._request()["gpu_type"], "A30")
        self.assertEqual([r[3] for r in self.rows()], [True, False, False, False, False])

    def test_waits_are_short_to_read(self):
        from datetime import datetime
        from ui.vscode_widget import friendly_wait
        now = datetime(2026, 11, 9, 19, 21, 0)
        for iso, text in (("2026-11-09T19:21:30", "now"), ("2026-11-09T19:41:18", "in 20 min"),
                          ("2026-11-10T06:09:34", "in 11 h"), ("2026-11-10T12:58:25", "in 18 h"),
                          ("2026-11-13T11:23:25", "in 3.7 days"), ("2026-11-09T18:00:00", "now"),
                          ("", ""), ("N/A", "N/A")):
            with self.subTest(iso=iso):
                self.assertEqual(friendly_wait(iso, now), text)

    def test_estimates_are_dropped_when_the_size_changes(self):
        self.pick_gpu("gpu32", "RTX6000")
        self.run_comparison()
        self.form.memory_combo.setCurrentText("96G")
        self.assertTrue(self.form.compare_box.isHidden())

    def test_not_offered_for_a_cpu_only_request(self):
        self.pick_gpu("cpu", None)
        self.assertTrue(self.form.compare_btn.isHidden())


class TestSubmit(FormTestCase):
    def test_submit_sends_exactly_what_the_form_shows(self):
        self.pick_gpu("gpu32", "RTX6000")
        self.form.memory_combo.setCurrentText("48G")
        self.form.time_combo.setCurrentText("8:00:00")
        with mock.patch.object(self.form, "_do_submit") as do_submit:
            self.form.submit_job()
        do_submit.assert_called_once()
        self.assertEqual(self.form._pending_submit, dict(
            cpus=8, memory="48G", gpu_type="RTX6000", gpu_count=1, account="pi_lab_gpu",
            time_limit="8:00:00", use_free=True, family="gpu32", exclude_nodes=[]))

    def test_the_last_request_is_restored_next_time(self):
        self.pick_gpu("gpu", "A30")
        self.form.memory_combo.setCurrentText("24G")
        self.form.time_combo.setCurrentText("4:00:00")
        with mock.patch.object(self.form, "_do_submit"):
            self.form.submit_job()
        again = self.make_form()
        req = again._request()
        self.assertEqual((req["family"], req["gpu_type"], req["memory"], req["time_limit"]),
                         ("gpu", "A30", "24G", "4:00:00"))
        # The account is not remembered: it is re-chosen from today's priorities.
        self.assertEqual(req["account"], "pi_lab_gpu")


if __name__ == "__main__":
    unittest.main()
