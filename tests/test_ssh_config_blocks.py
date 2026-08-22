#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Managed-block arithmetic on ~/.ssh/config.

The regression these cover: a compute node gets reallocated to a new job, we
write a fresh block and append it, but the previous job's block for the same node
stays. OpenSSH takes the FIRST value it finds for each keyword, so the oldest,
deadest block wins and connections run through a jump host whose job has ended.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "hpc3_launcher"))

from modules.ssh_config_blocks import node_of_block, strip_blocks_for_node  # noqa: E402


def block(job_id, node):
    return f"""
# === BEGIN HPC3 Launcher VSCode (JobID: {job_id}) ===

Host hpc_login_{job_id}
    HostName hpc3.rcic.uci.edu
    User someone
    IdentityFile /dev/null


Host {node}
    HostName {node}
    User someone
    Port 22
    IdentityFile /dev/null
    ProxyJump hpc_login_{job_id}
    StrictHostKeyChecking accept-new
# === END HPC3 Launcher VSCode (JobID: {job_id}) ===
"""


class TestNodeOfBlock(unittest.TestCase):
    def test_picks_the_node_not_the_jump_host(self):
        self.assertEqual(node_of_block(block("111", "gpu-a")), "gpu-a")

    def test_returns_none_when_there_is_no_node(self):
        self.assertIsNone(node_of_block("Host hpc_login_111\n"))


class TestStripBlocksForNode(unittest.TestCase):
    def test_removes_every_block_for_that_node(self):
        text = block("111", "gpu-a") + block("222", "gpu-a")
        out, removed = strip_blocks_for_node(text, "gpu-a")
        self.assertEqual(removed, 2)
        self.assertNotIn("gpu-a", out)

    def test_leaves_other_nodes_alone(self):
        text = block("111", "gpu-a") + block("222", "gpu-b")
        out, removed = strip_blocks_for_node(text, "gpu-a")
        self.assertEqual(removed, 1)
        self.assertIn("hpc_login_222", out)
        self.assertNotIn("hpc_login_111", out)

    def test_can_spare_one_job(self):
        text = block("111", "gpu-a") + block("222", "gpu-a")
        out, removed = strip_blocks_for_node(text, "gpu-a", keep_job_id="222")
        self.assertEqual(removed, 1)
        self.assertIn("hpc_login_222", out)
        self.assertNotIn("hpc_login_111", out)

    def test_never_touches_hand_written_config(self):
        mine = "Host my-server\n    HostName example.com\n"
        text = mine + block("111", "gpu-a")
        out, removed = strip_blocks_for_node(text, "gpu-a")
        self.assertEqual(removed, 1)
        self.assertIn("Host my-server", out)

    def test_nothing_to_do_is_a_no_op(self):
        text = block("111", "gpu-a")
        out, removed = strip_blocks_for_node(text, "gpu-zzz")
        self.assertEqual(removed, 0)
        self.assertIs(out, text)

    def test_also_matches_the_legacy_marker(self):
        legacy = block("111", "gpu-a").replace(
            "HPC3 Launcher VSCode", "HPC App VSCode Configuration")
        out, removed = strip_blocks_for_node(legacy, "gpu-a")
        self.assertEqual(removed, 1)
        self.assertNotIn("gpu-a", out)


@unittest.skipUnless(shutil.which("ssh"), "needs the ssh client")
class TestAgainstRealSsh(unittest.TestCase):
    """What OpenSSH actually resolves — the behaviour the whole fix rests on."""

    def _proxyjump(self, config_text, host):
        with tempfile.NamedTemporaryFile("w", suffix=".sshconfig", delete=False) as fh:
            fh.write(config_text)
            path = fh.name
        try:
            out = subprocess.run(["ssh", "-F", path, "-G", host],
                                 capture_output=True, text=True, timeout=15).stdout
            for line in out.splitlines():
                if line.startswith("proxyjump "):
                    return line.split(None, 1)[1].strip()
            return None
        finally:
            os.unlink(path)

    def test_first_block_wins_which_is_why_stale_ones_matter(self):
        # appended newest-last, exactly how the launcher writes them
        text = block("111", "gpu-a") + block("999", "gpu-a")
        self.assertEqual(self._proxyjump(text, "gpu-a"), "hpc_login_111",
                         "ssh should use the FIRST block; if not, the premise is wrong")

    def test_after_stripping_the_live_job_is_the_one_used(self):
        text = block("111", "gpu-a") + block("999", "gpu-a")
        out, _ = strip_blocks_for_node(text, "gpu-a", keep_job_id="999")
        self.assertEqual(self._proxyjump(out, "gpu-a"), "hpc_login_999")


if __name__ == "__main__":
    unittest.main()
