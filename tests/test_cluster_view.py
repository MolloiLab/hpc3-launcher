#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for ``modules/cluster_view``: the paid / free / idle picture of the GPU
nodes, what fits on them right now, and the free-queue priority arithmetic.

``fixtures/cluster_snapshot.txt`` is real HPC3 output (sinfo + squeue + sshare,
2026-11-09) with user and account names replaced, so the parsers are judged by
what Slurm actually prints rather than by what we imagine it prints.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "hpc3_launcher"))

from modules import cluster_view as cv  # noqa: E402


def load_snapshot(username=None):
    with open(os.path.join(HERE, "fixtures", "cluster_snapshot.txt"), encoding="utf-8") as fh:
        return cv.build_snapshot(fh.read(), username)


def node(**overrides):
    """A healthy 4x RTX6000 node with everything idle, to be bent per test."""
    base = {
        "name": "hpc3-gpu-n54-00", "partitions": ["gpu32", "free-gpu32"], "state": "mixed",
        "cpus_alloc": 0, "cpus_idle": 32, "cpus_other": 0, "cpus_total": 32,
        "mem_mb": 257000, "alloc_mem_mb": 0,
        "gpu_model": "RTX6000", "gpu_total": 4, "gpu_used": 0,
        "gpu_paid": 0, "gpu_free": 0,
    }
    base.update(overrides)
    return base


class TestSmallParsers(unittest.TestCase):
    def test_gres(self):
        self.assertEqual(cv.parse_gres("gpu:RTX6000:4"), ("RTX6000", 4))
        self.assertEqual(cv.parse_gres("gpu:L40S:4(S:0-1)"), ("L40S", 4))
        self.assertEqual(cv.parse_gres("(null)"), ("", 0))
        self.assertEqual(cv.parse_gres(None), ("", 0))

    def test_gres_used_counts_gpus_not_index_pieces(self):
        # The old parser split the index list: "0-3" has two pieces, so a node
        # with all four GPUs busy was reported as half empty.
        self.assertEqual(cv.parse_gres_used("gpu:V100:4(IDX:0-3)"), 4)
        self.assertEqual(cv.parse_gres_used("gpu:RTX6000:3(IDX:0-2)"), 3)
        self.assertEqual(cv.parse_gres_used("gpu:RTX6000:2(IDX:0,3)"), 2)
        self.assertEqual(cv.parse_gres_used("gpu:V100:1(IDX:0)"), 1)
        self.assertEqual(cv.parse_gres_used("gpu:A30:0(IDX:N/A)"), 0)

    def test_job_gpus(self):
        self.assertEqual(cv.parse_job_gpus("gres/gpu:RTX6000:2"), 2)
        self.assertEqual(cv.parse_job_gpus("gres/gpu:L40S:1"), 1)
        self.assertEqual(cv.parse_job_gpus("gres/gpu:1"), 1)
        self.assertEqual(cv.parse_job_gpus("gres/gpu:3"), 3)
        self.assertEqual(cv.parse_job_gpus("gres/gpu"), 1)       # count omitted = 1
        self.assertEqual(cv.parse_job_gpus("gres/gpu:A30"), 1)
        self.assertEqual(cv.parse_job_gpus("N/A"), 0)
        self.assertEqual(cv.parse_job_gpus(""), 0)

    def test_expand_hostlist(self):
        self.assertEqual(cv.expand_hostlist("hpc3-gpu-n54-01"), ["hpc3-gpu-n54-01"])
        self.assertEqual(
            cv.expand_hostlist("hpc3-gpu-k54-[06-08],hpc3-gpu-m54-01"),
            ["hpc3-gpu-k54-06", "hpc3-gpu-k54-07", "hpc3-gpu-k54-08", "hpc3-gpu-m54-01"])
        self.assertEqual(cv.expand_hostlist("hpc3-19-[00-01,03]"),
                         ["hpc3-19-00", "hpc3-19-01", "hpc3-19-03"])
        self.assertEqual(cv.expand_hostlist(""), [])

    def test_base_state(self):
        self.assertEqual(cv.base_state("mixed-"), "mixed")
        self.assertEqual(cv.base_state("down*"), "down")
        self.assertEqual(cv.base_state("idle~"), "idle")
        self.assertEqual(cv.base_state("draining+reserved"), "draining")


class TestSnapshotFromRealOutput(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snap = load_snapshot()
        cls.nodes = cls.snap["nodes"]

    def test_every_gpu_is_paid_free_or_idle(self):
        gpu_nodes = [n for n in self.nodes.values() if n["gpu_total"]]
        self.assertEqual(len(gpu_nodes), 46)
        for n in gpu_nodes:
            with self.subTest(node=n["name"]):
                idle = n["gpu_total"] - n["gpu_used"]
                self.assertGreaterEqual(idle, 0)
                self.assertEqual(n["gpu_paid"] + n["gpu_free"] + idle, n["gpu_total"])

    def test_a_node_is_merged_across_its_partitions(self):
        self.assertEqual(sorted(self.nodes["hpc3-gpu-n54-03"]["partitions"]),
                         ["free-gpu32", "gpu32"])
        # Takes free jobs but is not in the ordinary paid `gpu` partition.
        k54 = self.nodes["hpc3-gpu-k54-00"]["partitions"]
        self.assertIn("free-gpu", k54)
        self.assertNotIn("gpu", k54)

    def test_model_totals(self):
        models = {m["model"]: m for m in self.snap["models"]}
        self.assertEqual([m["model"] for m in self.snap["models"]],
                         ["L40S", "RTX6000", "V100", "A30", "A100"])
        self.assertEqual(models["RTX6000"]["total"], 28)
        self.assertEqual(models["L40S"]["total"], 12)
        self.assertEqual(models["A100"]["total"], 8)
        for m in models.values():
            self.assertEqual(m["paid"] + m["free"] + m["idle"], m["total"])
            self.assertLessEqual(m["startable"], m["idle"])

    def test_idle_is_not_the_same_as_startable(self):
        # Five RTX6000s were idle; only two sat on a node with cores left.
        rtx = next(m for m in self.snap["models"] if m["model"] == "RTX6000")
        self.assertEqual((rtx["idle"], rtx["startable"]), (5, 2))

    def test_room_on_known_nodes(self):
        self.assertEqual(cv.room(self.nodes["hpc3-gpu-n54-00"]),
                         {"gpus": 1, "cpus": 9, "mem_gb": 54})
        # One idle GPU but two idle cores: 2 cores x 6 GB/core caps the memory.
        self.assertEqual(cv.room(self.nodes["hpc3-gpu-m54-02"]),
                         {"gpus": 1, "cpus": 2, "mem_gb": 12})
        # Idle GPUs on a node with every core allocated: nothing can start.
        self.assertEqual(cv.room(self.nodes["hpc3-gpu-n54-02"])["gpus"], 0)

    def test_fit_report(self):
        # The request that was actually stuck in the queue: 2 GPUs + 128 GB.
        big = cv.fit_report(self.nodes, "gpu32", "RTX6000", 2, 22, 128)
        self.assertEqual(big["fits"], [])
        self.assertIsNone(big["best"])
        self.assertEqual(big["idle_gpus"], 5)
        small = cv.fit_report(self.nodes, "gpu32", "RTX6000", 1, 3, 16)
        self.assertEqual(small["fits"], ["hpc3-gpu-n54-00"])
        self.assertEqual(small["best"]["name"], "hpc3-gpu-n54-00")
        # One GPU but too much memory: nothing fits, and `best` says what would.
        medium = cv.fit_report(self.nodes, "gpu32", "RTX6000", 1, 16, 96)
        self.assertEqual(medium["fits"], [])
        self.assertEqual((medium["best"]["cpus"], medium["best"]["mem_gb"]), (9, 54))

    def test_fit_report_any_model_in_family(self):
        report = cv.fit_report(self.nodes, "gpu32", "", 1, 2, 12)
        self.assertEqual(report["fits"], ["hpc3-gpu-m54-02", "hpc3-gpu-n54-00"])

    def test_fit_report_leaves_out_nodes_the_job_is_kept_off(self):
        # "Fits right now on n54-00" is no use if n54-00 is the broken node.
        report = cv.fit_report(self.nodes, "gpu32", "RTX6000", 1, 3, 16,
                               exclude=["hpc3-gpu-n54-00"])
        self.assertEqual(report["fits"], [])
        self.assertEqual(report["avoided_idle"], 1)
        self.assertEqual(report["idle_gpus"], 4)
        self.assertNotEqual((report["best"] or {}).get("name"), "hpc3-gpu-n54-00")
        self.assertEqual(cv.fit_report(self.nodes, "gpu32", "RTX6000", 1, 3, 16)
                         ["avoided_idle"], 0)

    def test_nodes_other_users_avoid(self):
        # Six different people keep their queued jobs off n54-00; counted per user,
        # however many array tasks each has waiting.
        avoided = self.snap["avoided"]
        self.assertEqual(avoided["hpc3-gpu-n54-00"], 6)
        self.assertEqual(avoided["hpc3-gpu-n54-01"], 2)
        self.assertEqual(avoided["hpc3-gpu-k54-07"], 1)
        self.assertNotIn("hpc3-gpu-n54-02", avoided)
        self.assertEqual(cv.suspect_nodes(avoided), ["hpc3-gpu-n54-00"])

    def test_own_gpus_are_counted(self):
        snap = load_snapshot("user07")
        mine = sum(n.get("gpu_mine", 0) for n in snap["nodes"].values())
        self.assertGreater(mine, 0)
        self.assertEqual(sum(n.get("gpu_mine", 0) for n in self.nodes.values()), 0)

    def test_queue_summary(self):
        rows = {r["partition"]: r for r in self.snap["queue"]}
        self.assertEqual(rows["free-gpu32"]["pending"], 60)
        self.assertEqual(rows["free-gpu32"]["competing"] + rows["free-gpu32"]["blocked"], 60)
        self.assertEqual(rows["gpu32"]["competing"], 0)   # both were held by their owner

    def test_fairshare(self):
        self.assertEqual(self.snap["fairshare"], {
            "demo": 0.371566, "pi_lab": 0.380101, "pi_lab_gpu": 1.0, "pi_lab_gpu32": 0.13657})


class TestRoomRules(unittest.TestCase):
    def test_all_idle(self):
        # 32 idle cores at 6 GB/core, on a 251 GB node.
        self.assertEqual(cv.room(node()), {"gpus": 4, "cpus": 32, "mem_gb": 192})

    def test_unusable_states(self):
        for state in ("down*", "drained", "draining", "reserved", "allocated",
                      "completing", "maint"):
            with self.subTest(state=state):
                self.assertEqual(cv.room(node(state=state))["gpus"], 0)
        self.assertEqual(cv.room(node(state="mixed-"))["gpus"], 4)
        self.assertEqual(cv.room(node(state="idle"))["gpus"], 4)

    def test_needs_two_cores_per_gpu(self):
        self.assertEqual(cv.room(node(cpus_idle=1))["gpus"], 0)
        self.assertEqual(cv.room(node(cpus_idle=5, gpu_used=1)),
                         {"gpus": 2, "cpus": 5, "mem_gb": 30})

    def test_needs_memory(self):
        self.assertEqual(cv.room(node(alloc_mem_mb=257000))["gpus"], 0)
        self.assertEqual(cv.room(node(alloc_mem_mb=257000 - 20 * 1024))["mem_gb"], 20)

    def test_no_idle_gpu(self):
        self.assertEqual(cv.room(node(gpu_used=4))["gpus"], 0)

    def test_paid_only_node_has_no_room_for_a_free_job(self):
        paid_only = node(partitions=["gpu32"])
        self.assertEqual(cv.room(paid_only, use_free=True)["gpus"], 0)
        self.assertEqual(cv.room(paid_only, use_free=False)["gpus"], 4)

    def test_memory_cap_follows_the_partition(self):
        v100 = node(gpu_model="V100", partitions=["gpu", "free-gpu"], cpus_idle=10,
                    cpus_total=40, mem_mb=192000)
        self.assertEqual(cv.room(v100)["mem_gb"], 90)    # 10 cores x 9 GB on free-gpu

    def test_request_fits(self):
        n = node(cpus_idle=9, gpu_used=3, alloc_mem_mb=257000 - 60 * 1024)
        self.assertTrue(cv.request_fits(n, 1, 8, 48))
        self.assertFalse(cv.request_fits(n, 2, 8, 48))
        self.assertFalse(cv.request_fits(n, 1, 10, 48))
        self.assertFalse(cv.request_fits(n, 1, 8, 64))


class TestQueueAndPriority(unittest.TestCase):
    PENDING = ("free-gpu32|Priority|900|gres/gpu:RTX6000:1|a\n"
               "free-gpu32|Resources|2110|gres/gpu:1|b\n"
               "free-gpu32|JobArrayTaskLimit|2112|gres/gpu:RTX6000:1|c\n"
               "free-gpu32|MaxGRESPerAccount|2111|gres/gpu:RTX6000:1|c\n"
               "free-gpu,free-gpu32|Priority|400|gres/gpu:1|d\n"
               "gpu|Dependency|5000|gres/gpu:1|e\n"
               "garbage line\n")

    def test_parse_and_summarise(self):
        pending = cv.parse_pending(self.PENDING)
        self.assertEqual(len(pending), 6)
        rows = {r["partition"]: r for r in cv.queue_summary(pending)}
        self.assertEqual(rows["free-gpu32"],
                         {"partition": "free-gpu32", "pending": 5, "competing": 3,
                          "blocked": 2, "top_priority": 2110})
        self.assertEqual(rows["free-gpu"]["pending"], 1)   # the two-partition job
        self.assertEqual(rows["gpu"]["competing"], 0)

    def test_jobs_ahead_ignores_jobs_held_by_their_own_limits(self):
        pending = cv.parse_pending(self.PENDING)
        # 2111 and 2112 outrank us on paper but are throttled, not in line.
        self.assertEqual(cv.jobs_ahead(pending, "free-gpu32", 2110), (0, 3))
        self.assertEqual(cv.jobs_ahead(pending, "free-gpu32", 383), (3, 3))
        self.assertEqual(cv.jobs_ahead(pending, "gpu32", 383), (0, 0))

    def test_free_priority_matches_what_sprio_reported(self):
        # sprio for a real free job under fair share 0.13657: 273 + 100 + 10 = 383.
        self.assertEqual(cv.free_priority(0.13657), 383)
        self.assertEqual(cv.free_priority(1.0), 2110)

    def test_exclusions_are_parsed_and_your_own_do_not_count(self):
        out = ("free-gpu32|Priority|900|gres/gpu:1|a|hpc3-gpu-n54-[00-01]\n"
               "free-gpu32|Priority|900|gres/gpu:1|a|hpc3-gpu-n54-00\n"
               "free-gpu32|Priority|800|gres/gpu:1|b|hpc3-gpu-k54-[02,06-08],hpc3-gpu-n54-00\n"
               "free-gpu32|Priority|700|gres/gpu:1|me|hpc3-gpu-m54-02\n"
               "free-gpu32|Priority|600|gres/gpu:1|c|\n"
               "free-gpu32|Priority|600|gres/gpu:1|d|(null)\n"
               "free-gpu32|Priority|500|gres/gpu:1|old-five-column-line\n")
        pending = cv.parse_pending(out)
        self.assertEqual(len(pending), 7)
        self.assertEqual(pending[2]["excluded"],
                         ["hpc3-gpu-k54-02", "hpc3-gpu-k54-06", "hpc3-gpu-k54-07",
                          "hpc3-gpu-k54-08", "hpc3-gpu-n54-00"])
        self.assertEqual([pending[i]["excluded"] for i in (4, 5, 6)], [[], [], []])
        avoided = cv.avoided_nodes(pending, "me")
        self.assertEqual(avoided["hpc3-gpu-n54-00"], 2)      # a (twice) and b
        self.assertEqual(avoided["hpc3-gpu-n54-01"], 1)
        self.assertNotIn("hpc3-gpu-m54-02", avoided)         # only you avoid it
        self.assertEqual(cv.suspect_nodes(avoided), [])
        self.assertEqual(cv.suspect_nodes(avoided, minimum=2), ["hpc3-gpu-n54-00"])

    def test_account_names_match_whatever_their_case(self):
        # sbank prints SYMOLLOI_LAB_GPU; sshare prints symolloi_lab_gpu. Matching
        # them by exact case found no best account at all.
        shares = cv.parse_fairshare("demo|0.37\nPI_LAB_GPU|1.000000\npi_lab_gpu32|0.14\n")
        self.assertEqual(shares, {"demo": 0.37, "pi_lab_gpu": 1.0, "pi_lab_gpu32": 0.14})
        names = ["DEMO", "PI_LAB_GPU", "PI_LAB_GPU32"]
        self.assertEqual(cv.best_free_account(names, shares), "PI_LAB_GPU")
        self.assertEqual(cv.account_share("Pi_Lab_Gpu32", shares), 0.14)
        self.assertIsNone(cv.account_share("nobody", shares))

    def test_best_free_account(self):
        shares = {"demo": 0.37, "pi_lab": 0.38, "pi_lab_gpu": 1.0, "pi_lab_gpu32": 0.14}
        self.assertEqual(cv.best_free_account(list(shares), shares), "pi_lab_gpu")
        self.assertEqual(cv.best_free_account(["demo", "pi_lab_gpu32"], shares), "demo")
        self.assertIsNone(cv.best_free_account(["someone_else"], shares))
        self.assertIsNone(cv.best_free_account([], {}))

    def test_parse_fairshare_skips_junk(self):
        self.assertEqual(cv.parse_fairshare("a|0.5\nbad\nb|x\n\nc|1.000000\n"),
                         {"a": 0.5, "c": 1.0})


class TestTestOnly(unittest.TestCase):
    def test_estimate(self):
        out = ("sbatch: Job 59409075 to start at 2026-11-11T09:40:37 a using 2 processors "
               "on nodes hpc3-gpu-n54-02 in partition free-gpu32\n")
        self.assertEqual(cv.parse_test_only(out), {
            "ok": True, "start": "2026-11-11T09:40:37", "node": "hpc3-gpu-n54-02",
            "message": ""})

    def test_rejection(self):
        out = ("sbatch: error: !!! slurm_job_submit ERROR: 1 submitted a job to a gpu32 "
               "partition, without specifying a GPU32 Account.\n"
               "allocation failure: Unspecified error\n")
        result = cv.parse_test_only(out)
        self.assertFalse(result["ok"])
        self.assertIn("without specifying a GPU32 Account", result["message"])
        self.assertFalse(cv.parse_test_only("")["ok"])


class TestInsightsIsReadOnly(unittest.TestCase):
    """The class that talks to the cluster must only ever ask questions."""

    class FakeSession:
        def __init__(self, output=""):
            self.commands, self.output = [], output

        def run(self, command, reporter=None):
            self.commands.append(command)
            return self.output

    def test_snapshot_runs_only_queries_and_caches(self):
        session = self.FakeSession()
        insights = cv.ClusterInsights(session, "demo")
        insights.snapshot()
        insights.snapshot()                     # served from the cache
        self.assertEqual(len(session.commands), 1)
        insights.snapshot(force=True)
        self.assertEqual(len(session.commands), 2)
        for command in session.commands:
            for piece in command.split(";"):
                word = piece.strip().split()[0]
                self.assertIn(word, ("sinfo", "squeue", "sshare", "echo"))

    def test_estimate_refuses_a_real_submission(self):
        session = self.FakeSession("sbatch: Job 1 to start at 2026-11-11T09:40:37")
        insights = cv.ClusterInsights(session, "demo")
        with self.assertRaises(ValueError):
            insights.estimate_start("sbatch -p free-gpu32 /opt/rcic/scripts/vscode-sshd.sh")
        self.assertEqual(session.commands, [])
        result = insights.estimate_start("sbatch --test-only -p free-gpu32 x.sh")
        self.assertTrue(result["ok"])

    def test_several_estimates_come_back_in_order(self):
        output = ("sbatch: Job 1 to start at 2026-11-10T06:09:34 a using 6 processors on "
                  "nodes hpc3-gpu-l54-03 in partition free-gpu\n" + cv._SEP + "\n"
                  "sbatch: error: Batch job submission failed: Requested node "
                  "configuration is not available\n" + cv._SEP + "\n"
                  "sbatch: Job 3 to start at 2026-11-09T19:41:18 a using 6 processors on "
                  "nodes hpc3-gpu-k54-05 in partition free-gpu\n")
        session = self.FakeSession(output)
        insights = cv.ClusterInsights(session, "demo")
        results = insights.estimate_starts(["sbatch --test-only a.sh"] * 3)
        self.assertEqual([r["ok"] for r in results], [True, False, True])
        self.assertEqual(results[2]["node"], "hpc3-gpu-k54-05")
        self.assertEqual(len(session.commands), 1)            # one round trip
        self.assertEqual(session.commands[0].count("sbatch --test-only"), 3)
        # One real submission among them poisons the whole batch.
        with self.assertRaises(ValueError):
            insights.estimate_starts(["sbatch --test-only a.sh", "sbatch a.sh"])
        self.assertEqual(len(session.commands), 1)
        self.assertEqual(insights.estimate_starts([]), [])
        # A reply that was cut short still answers every question.
        short = cv.ClusterInsights(self.FakeSession("garbage"), "demo")
        self.assertEqual([r["ok"] for r in short.estimate_starts(["sbatch --test-only a"] * 2)],
                         [False, False])


if __name__ == "__main__":
    unittest.main()
