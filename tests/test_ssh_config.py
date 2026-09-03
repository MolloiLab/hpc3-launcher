#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression tests for the ~/.ssh/config writer.

The bug these exist for: Slurm's ``sacct`` reports a job that never got an
allocation as NodeList="None assigned". The app treated that string as a hostname
and wrote ``HostName None assigned`` into the user's config, which OpenSSH rejects
with "keyword hostname extra arguments at end of line" -- and then *terminates*,
abandoning the whole file, so every host the user had stopped resolving.

Where OpenSSH is available these tests check the generated config against the real
``ssh -G`` parser rather than trusting our own idea of what is valid.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "hpc3_launcher"))

from modules.vscode_helper import (  # noqa: E402
    VSCodeManager, clean_node_name, clean_port, is_terminal_state,
    normalize_job_state, prune_broken_blocks,
)

SSH = shutil.which("ssh")


def ssh_parses(config_text):
    """(ok, stderr) from asking the real OpenSSH client to parse a config file."""
    with tempfile.NamedTemporaryFile("w", suffix="_config", delete=False) as handle:
        handle.write(config_text)
        path = handle.name
    try:
        os.chmod(path, 0o600)
        proc = subprocess.run([SSH, "-F", path, "-G", "example-host"],
                              capture_output=True, text=True)
        return proc.returncode == 0, proc.stderr
    finally:
        os.unlink(path)


class TestSlurmValueCleaning(unittest.TestCase):
    def test_sacct_no_allocation_sentinel_is_not_a_hostname(self):
        # The exact string that broke a user's config.
        self.assertIsNone(clean_node_name("None assigned"))

    def test_other_no_node_sentinels(self):
        for value in (None, "", "  ", "(None)", "None", "(null)", "n/a", "Unknown"):
            self.assertIsNone(clean_node_name(value), value)

    def test_real_node_names_survive(self):
        for value in ("hpc3-gpu-n54-01", "hpc3-14-01", "login-1", " hpc3-gpu-k54-00 "):
            self.assertEqual(clean_node_name(value), value.strip())

    def test_anything_with_whitespace_or_quotes_is_rejected(self):
        for value in ("node one", 'node"x', "node\nx", "-leading-dash", "node;rm -rf /"):
            self.assertIsNone(clean_node_name(value), value)

    def test_port_cleaning(self):
        self.assertEqual(clean_port("8022"), "8022")
        self.assertEqual(clean_port(None), "22")
        self.assertEqual(clean_port("None"), "22")
        self.assertEqual(clean_port("99999"), "22")


class TestJobState(unittest.TestCase):
    def test_sacct_decorated_cancelled_is_terminal(self):
        # squeue says "CANCELLED"; sacct says "CANCELLED by <uid>". The old
        # `status in (...)` check missed the second form, so dead sessions stayed
        # in the table and could be selected -- which is what wrote the bad block.
        self.assertTrue(is_terminal_state("CANCELLED by 3012547"))
        self.assertEqual(normalize_job_state("CANCELLED by 3012547"), "CANCELLED")

    def test_plain_states(self):
        for state in ("COMPLETED", "FAILED", "TIMEOUT", "NODE_FAIL", "OUT_OF_MEMORY"):
            self.assertTrue(is_terminal_state(state), state)

    def test_live_states_are_not_terminal(self):
        for state in ("RUNNING", "PENDING", "CONFIGURING", "", None):
            self.assertFalse(is_terminal_state(state), state)


BROKEN_BLOCK = """
# === BEGIN HPC3 Launcher VSCode (JobID: 55726278) ===

Host hpc_login_55726278
    HostName hpc3.rcic.uci.edu
    User jinx19
    IdentityFile "C:\\Users\\jinx19\\.ssh\\jinx19_hpc_app_key"


Host None assigned
    HostName None assigned
    User jinx19
    Port 22
    ProxyJump hpc_login_55726278
    StrictHostKeyChecking accept-new
# === END HPC3 Launcher VSCode (JobID: 55726278) ===
"""

GOOD_BLOCK = """
# === BEGIN HPC3 Launcher VSCode (JobID: 55726259) ===

Host hpc_login_55726259
    HostName hpc3.rcic.uci.edu
    User jinx19
    IdentityFile "C:\\Users\\Jo Smith\\.ssh\\jinx19_hpc_app_key"


Host hpc3-gpu-n54-01
    HostName hpc3-gpu-n54-01
    User jinx19
    Port 22
    IdentityFile "C:\\Users\\Jo Smith\\.ssh\\jinx19_hpc_app_key"
    ProxyJump hpc_login_55726259
    StrictHostKeyChecking accept-new
# === END HPC3 Launcher VSCode (JobID: 55726259) ===
"""

USER_BLOCK = """Host my-own-server
    HostName example.com
    User someone
"""


class TestPruneBrokenBlocks(unittest.TestCase):
    def test_broken_block_is_removed(self):
        cleaned, dropped = prune_broken_blocks(USER_BLOCK + BROKEN_BLOCK + GOOD_BLOCK)
        # Either malformed line in the block is enough to condemn the whole block.
        self.assertTrue(any("None assigned" in line for line in dropped), dropped)
        self.assertNotIn("None assigned", cleaned)

    def test_good_block_and_user_config_are_untouched(self):
        cleaned, _ = prune_broken_blocks(USER_BLOCK + BROKEN_BLOCK + GOOD_BLOCK)
        self.assertIn("hpc3-gpu-n54-01", cleaned)
        self.assertIn("Host my-own-server", cleaned)

    def test_quoted_windows_path_with_a_space_is_not_mistaken_for_garbage(self):
        _, dropped = prune_broken_blocks(GOOD_BLOCK)
        self.assertEqual(dropped, [])

    def test_a_block_holding_foreign_directives_is_left_alone(self):
        """If an END marker ever went missing the regex over-matches into the user's
        own config -- and `Host dev prod staging` is a legitimate 3-token line. Never
        delete a block we cannot prove is entirely ours."""
        swallowed = BROKEN_BLOCK.replace(
            "    User jinx19\n    Port 22",
            "    User jinx19\n    Compression yes\n    Port 22")
        cleaned, dropped = prune_broken_blocks(swallowed)
        self.assertEqual(dropped, [])
        self.assertEqual(cleaned, swallowed)

    def test_a_users_multi_pattern_host_line_is_never_collateral_damage(self):
        swallowed = BROKEN_BLOCK.replace(
            "Host None assigned",
            "Host dev prod staging\n    LocalForward 8080 localhost:80\n\nHost None assigned")
        _, dropped = prune_broken_blocks(swallowed)
        self.assertEqual(dropped, [])

    def test_nothing_to_do_is_a_no_op(self):
        cleaned, dropped = prune_broken_blocks(USER_BLOCK + GOOD_BLOCK)
        self.assertEqual(dropped, [])
        self.assertEqual(cleaned, USER_BLOCK + GOOD_BLOCK)


@unittest.skipIf(SSH is None, "no ssh client available")
class TestAgainstRealOpenSSH(unittest.TestCase):
    def test_the_reported_config_really_is_rejected(self):
        """Guards the premise: this is the failure the user hit."""
        ok, stderr = ssh_parses(USER_BLOCK + BROKEN_BLOCK)
        self.assertFalse(ok)
        self.assertIn("extra arguments", stderr)

    def test_pruning_makes_it_parseable_again(self):
        cleaned, _ = prune_broken_blocks(USER_BLOCK + BROKEN_BLOCK + GOOD_BLOCK)
        ok, stderr = ssh_parses(cleaned)
        self.assertTrue(ok, stderr)

    def test_a_windows_path_with_a_space_parses(self):
        ok, stderr = ssh_parses(GOOD_BLOCK)
        self.assertTrue(ok, stderr)

    def test_unquoted_windows_path_with_a_space_would_have_broken_it(self):
        """Why IdentityFile is quoted: the unquoted form is a latent Windows bug."""
        ok, stderr = ssh_parses(GOOD_BLOCK.replace('"', ''))
        self.assertFalse(ok)
        self.assertIn("extra arguments", stderr)


class TestWriterRefusesBadInput(unittest.TestCase):
    """End-to-end: the manager must never put an unusable node in the config."""

    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.real_home = os.environ.get("HOME")
        os.environ["HOME"] = self.home
        os.environ["USERPROFILE"] = self.home
        os.makedirs(os.path.join(self.home, ".ssh"), mode=0o700)
        self.config = os.path.join(self.home, ".ssh", "config")
        self.manager = VSCodeManager(hostname="hpc3.rcic.uci.edu", username="jinx19",
                                     key_path=os.path.join(self.home, ".ssh", "k"))

    def tearDown(self):
        if self.real_home is not None:
            os.environ["HOME"] = self.real_home
        shutil.rmtree(self.home, ignore_errors=True)

    def read(self):
        if not os.path.exists(self.config):
            return ""
        with open(self.config) as handle:
            return handle.read()

    def test_none_assigned_is_never_written(self):
        self.manager._add_ssh_config_to_local("55726278",
                                              {"hostname": "None assigned", "port": "22"})
        self.assertNotIn("None assigned", self.read())

    def test_a_real_node_is_written_and_parses(self):
        self.manager._add_ssh_config_to_local("55726259",
                                              {"hostname": "hpc3-gpu-n54-01", "port": "22"})
        text = self.read()
        self.assertIn("Host hpc3-gpu-n54-01", text)
        if SSH:
            ok, stderr = ssh_parses(text)
            self.assertTrue(ok, stderr)

    def test_a_bogus_port_falls_back_to_22(self):
        self.manager._add_ssh_config_to_local("55726260",
                                              {"hostname": "hpc3-gpu-n54-02", "port": None})
        self.assertIn("Port 22", self.read())

    def test_writing_a_new_session_heals_a_config_broken_by_an_older_build(self):
        with open(self.config, "w") as handle:
            handle.write(USER_BLOCK + BROKEN_BLOCK)
        self.manager._add_ssh_config_to_local("55726259",
                                              {"hostname": "hpc3-gpu-n54-01", "port": "22"})
        text = self.read()
        self.assertNotIn("None assigned", text)
        self.assertIn("Host my-own-server", text)
        self.assertIn("Host hpc3-gpu-n54-01", text)
        if SSH:
            ok, stderr = ssh_parses(text)
            self.assertTrue(ok, stderr)

    def test_startup_repair_heals_without_launching_anything(self):
        with open(self.config, "w") as handle:
            handle.write(USER_BLOCK + BROKEN_BLOCK)
        dropped = self.manager.repair_local_ssh_config()
        self.assertEqual(dropped, 1)
        self.assertNotIn("None assigned", self.read())
        self.assertIn("Host my-own-server", self.read())

    def test_repair_is_idempotent_and_leaves_clean_configs_alone(self):
        with open(self.config, "w") as handle:
            handle.write(USER_BLOCK + GOOD_BLOCK)
        before = self.read()
        self.assertEqual(self.manager.repair_local_ssh_config(), 0)
        self.assertEqual(self.read(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
