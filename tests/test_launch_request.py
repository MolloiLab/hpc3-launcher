#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for what a launch request may contain and the command it becomes.

Two things are pinned down here:

* ``hpc3_constraints`` -- which GPU each tier + account can reach. Paid partitions
  check the account's suffix; free partitions take any account. Either way a GPU
  model must never be paired with the other family's partition (A100 on gpu32,
  RTX6000 on gpu).
* ``build_sbatch_command`` -- the one string that is run by a shell on the login
  node, shared by the real submission and the ``--test-only`` estimate.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "hpc3_launcher"))

from modules import hpc3_constraints as hc  # noqa: E402
from modules.vscode_helper import build_sbatch_command, parse_vscode_queue  # noqa: E402

ACCOUNTS = ["demo", "pi_lab", "pi_lab_gpu", "pi_lab_gpu32"]


def models(rows):
    return [gpu for _label, _family, gpu in rows if gpu]


class TestPaidTier(unittest.TestCase):
    def test_options_follow_the_account_suffix(self):
        self.assertEqual(models(hc.tier_gpu_options("pi_lab_gpu", False)),
                         ["V100", "A30", "A100"])
        self.assertEqual(models(hc.tier_gpu_options("pi_lab_gpu32", False)),
                         ["L40S", "RTX6000"])
        for cpu_account in ("demo", "pi_lab"):
            self.assertEqual(hc.tier_gpu_options(cpu_account, False),
                             [("No GPU", "cpu", None)])

    def test_every_paid_row_lands_on_the_accounts_own_partition(self):
        expected = {"demo": "standard", "pi_lab": "standard",
                    "pi_lab_gpu": "gpu", "pi_lab_gpu32": "gpu32"}
        for account in ACCOUNTS:
            for _label, family, gpu in hc.tier_gpu_options(account, False):
                with self.subTest(account=account, gpu=gpu):
                    self.assertEqual(hc.partition_for(account, gpu, False, family),
                                     expected[account])


class TestFreeTier(unittest.TestCase):
    def test_every_account_is_offered_every_model(self):
        for account in ACCOUNTS:
            with self.subTest(account=account):
                self.assertEqual(models(hc.tier_gpu_options(account, True)),
                                 ["L40S", "RTX6000", "V100", "A30", "A100"])

    def test_a_model_is_never_paired_with_the_other_family(self):
        # The combination the form must make impossible: whatever account is
        # chosen, A100 resolves to free-gpu and RTX6000 to free-gpu32.
        for account in ACCOUNTS:
            for _label, family, gpu in hc.tier_gpu_options(account, True):
                if gpu not in hc.GPU_MODELS:
                    continue
                with self.subTest(account=account, gpu=gpu):
                    own = hc.GPU_MODELS[gpu]["family"]
                    self.assertEqual(family, own)
                    self.assertEqual(hc.partition_for(account, gpu, True, family),
                                     hc.FAMILIES[own]["free"])
                    # ...even if a caller passes the wrong family alongside it.
                    wrong = "gpu" if own == "gpu32" else "gpu32"
                    self.assertEqual(hc.partition_for(account, gpu, True, wrong),
                                     hc.FAMILIES[own]["free"])

    def test_any_rows(self):
        rows = {(family, gpu): label for label, family, gpu in
                hc.tier_gpu_options("pi_lab_gpu", True)}
        self.assertIn(("gpu32", ""), rows)
        self.assertIn(("gpu", ""), rows)
        self.assertIn(("anygpu", ""), rows)
        self.assertIn(("cpu", None), rows)
        self.assertEqual(hc.partition_for("pi_lab_gpu", "", True, "gpu32"), "free-gpu32")
        self.assertEqual(hc.partition_for("pi_lab_gpu", "", True, "gpu"), "free-gpu")
        self.assertEqual(hc.partition_for("pi_lab_gpu", "", True, "anygpu"),
                         "free-gpu,free-gpu32")
        self.assertEqual(hc.partition_for("pi_lab_gpu", None, True, "cpu"), "free")

    def test_any_gpu_at_all_is_free_only(self):
        with self.assertRaises(ValueError):
            hc.partition_for("pi_lab_gpu", "", False, "anygpu")

    def test_no_family_still_means_the_accounts_own(self):
        self.assertEqual(hc.partition_for("pi_lab_gpu32", "", True), "free-gpu32")
        self.assertEqual(hc.partition_for("pi_lab_gpu", "", False), "gpu")
        self.assertEqual(hc.partition_for("demo", None, True), "free")


class TestSizing(unittest.TestCase):
    def test_memory_sets_the_core_count_by_partition(self):
        # gpu32 allows 6 GB per core, gpu allows 9.
        self.assertEqual(hc.required_cpus(128, 2, "pi_lab_gpu", "RTX6000", True, "gpu32"), 22)
        self.assertEqual(hc.required_cpus(48, 1, "pi_lab_gpu", "RTX6000", True, "gpu32"), 8)
        self.assertEqual(hc.required_cpus(48, 1, "pi_lab_gpu", "A30", True, "gpu"), 6)
        self.assertEqual(hc.required_cpus(4, 2, "demo", "L40S", True, "gpu32"), 4)  # 2 per GPU

    def test_any_gpu_at_all_must_satisfy_the_stricter_partition(self):
        self.assertEqual(hc.mem_per_cpu_gb("demo", "", True, "anygpu"), 6)
        self.assertEqual(hc.required_cpus(48, 1, "demo", "", True, "anygpu"), 8)
        self.assertEqual(hc.max_gpus_for("", "anygpu"), 4)
        self.assertEqual(hc.max_cpus_for("", "anygpu"), 40)

    def test_su_cost(self):
        self.assertEqual(hc.su_cost_range(22, "RTX6000", 2, 8, True, "gpu32"), (0, 0))
        self.assertEqual(hc.su_cost_range(22, "RTX6000", 2, 8, False, "gpu32"), (1200, 1200))
        self.assertEqual(hc.su_cost_range(8, "A30", 1, 2, False, "gpu"), (80, 80))
        # "Any" on gpu32 may land on an L40S (32/h) or an RTX6000 (64/h).
        self.assertEqual(hc.su_cost_range(6, "", 1, 8, False, "gpu32"), (304, 560))
        self.assertEqual(hc.su_cost_range(16, None, 0, 10, False, "cpu"), (160, 160))


class TestSbatchCommand(unittest.TestCase):
    def test_free_gpu32_under_another_account(self):
        cmd = build_sbatch_command(cpus=8, memory="48G", gpu_type="RTX6000", gpu_count=1,
                                   account="pi_lab_gpu", time_limit="8:00:00",
                                   use_free=True, family="gpu32")
        self.assertEqual(
            cmd, "sbatch --cpus-per-task=8 --mem=48G --time=8:00:00 --account=pi_lab_gpu "
                 "-p free-gpu32 --gres=gpu:RTX6000:1 /opt/rcic/scripts/vscode-sshd.sh")

    def test_test_only_describes_the_same_job(self):
        kwargs = dict(cpus=8, memory="48G", gpu_type="RTX6000", account="pi_lab_gpu",
                      time_limit="8:00:00", use_free=True, family="gpu32",
                      exclude_nodes=["hpc3-gpu-n54-01"])
        real = build_sbatch_command(**kwargs)
        probe = build_sbatch_command(test_only=True, **kwargs)
        self.assertEqual(probe, real.replace("sbatch ", "sbatch --test-only ", 1))

    def test_any_gpu_at_all(self):
        cmd = build_sbatch_command(account="demo", gpu_type="", gpu_count=1, use_free=True,
                                   family="anygpu")
        self.assertIn(" -p free-gpu,free-gpu32 --gres=gpu:1 ", cmd)

    def test_explicit_cpu_family_is_respected_on_a_gpu_account(self):
        cmd = build_sbatch_command(account="pi_lab_gpu", gpu_type=None, use_free=True,
                                   family="cpu")
        self.assertIn(" -p free ", cmd)
        self.assertNotIn("--gres", cmd)

    def test_gpu_account_with_nothing_said_still_gets_a_gpu(self):
        cmd = build_sbatch_command(account="LAB_GPU32", use_free=False)
        self.assertIn(" -p gpu32 --gres=gpu:1 ", cmd)

    def test_hostile_values_never_reach_the_shell(self):
        bad = [dict(account="lab; rm -rf ~"), dict(account="$(id)"), dict(account=""),
               dict(memory="4G && curl evil"), dict(time_limit="1:00:00`id`"),
               dict(gpu_type="V100 --wrap=x"), dict(exclude_nodes=["n1; id"]),
               dict(cpus="2; id")]
        for extra in bad:
            with self.subTest(extra=extra):
                kwargs = dict(account="pi_lab_gpu", use_free=True, family="gpu32")
                kwargs.update(extra)
                with self.assertRaises(ValueError):
                    build_sbatch_command(**kwargs)


class TestSessionQueue(unittest.TestCase):
    def test_pending_and_running_rows(self):
        out = ("59408299|PENDING||free-gpu32|383|Priority|2026-11-12T01:41:49|pi_lab_gpu32|"
               "gres/gpu:RTX6000:2|3-00:00:00\n"
               "59408292|RUNNING|hpc3-gpu-n54-03|gpu32|622|None|2026-11-09T16:26:02|"
               "pi_lab_gpu32|gres/gpu:RTX6000:2|8:00:00\n"
               "59408342|PENDING||free-gpu32|383|Resources|N/A|pi_lab_gpu32|N/A|1:00:00\n"
               "not a job\n")
        pending, running, unknown = parse_vscode_queue(out)
        self.assertEqual(pending["job_id"], "59408299")
        self.assertIsNone(pending["node"])
        self.assertEqual((pending["partition"], pending["priority"], pending["reason"]),
                         ("free-gpu32", 383, "Priority"))
        self.assertEqual(pending["start_estimate"], "2026-11-12T01:41:49")
        self.assertEqual(running["node"], "hpc3-gpu-n54-03")
        self.assertIsNone(running["reason"])
        self.assertIsNone(unknown["start_estimate"])
        self.assertIsNone(unknown["tres"])

    def test_empty(self):
        self.assertEqual(parse_vscode_queue(""), [])
        self.assertEqual(parse_vscode_queue(None), [])


if __name__ == "__main__":
    unittest.main()
