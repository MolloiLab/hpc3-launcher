#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression tests for the ~/.ssh/config writer.

The bug these exist for: Slurm's ``sacct`` reports a job that never got an
allocation as NodeList="None assigned". The app treated that string as a hostname
and wrote ``HostName None assigned`` into the user's config, which OpenSSH rejects
with "keyword hostname extra arguments at end of line" -- and then *terminates*,
abandoning the whole file, so every host the user had stopped resolving.

These are written to be meaningful on Windows, not merely to pass there:

  * the fake HOME contains a space ("Jo Smith"), so every writer test exercises the
    quoted-IdentityFile path that an unquoted `C:\\Users\\Jo Smith\\...` would break;
  * configs are checked against the *real* ``ssh`` on the machine running the tests,
    so Windows CI judges them with Windows' own OpenSSH parser;
  * we look for OpenSSH's parse complaints rather than a non-zero exit, because on
    Windows ssh.exe may also object to a temp file's ACLs -- which would otherwise
    turn into a false pass (a bad config "failing" for the wrong reason).
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
    _NO_WINDOW, VSCodeManager, clean_node_name, clean_port, is_terminal_state,
    normalize_job_state, prune_broken_blocks,
)

SSH = shutil.which("ssh")
SSH_KEYGEN = shutil.which("ssh-keygen")
ON_WINDOWS = os.name == "nt"

# CI sets this. Without it a runner missing ssh would quietly skip every test
# that uses the real parser, and the suite would still go green -- the exact
# shape of "tested" that lets a bug like this one come back.
REQUIRE_SSH = os.environ.get("HPC3_REQUIRE_SSH") == "1"

# What OpenSSH says when it refuses a config file. "extra arguments" is the exact
# failure this module exists to prevent.
_PARSE_COMPLAINTS = (
    "extra arguments", "bad configuration option", "garbage at end of line",
    "missing argument", "terminating,",
)


def ssh_config_error(config_text):
    """Hand a config to the real ssh client; return its parse complaint, or None."""
    handle = tempfile.NamedTemporaryFile("w", suffix="_config", delete=False,
                                         newline="")
    try:
        handle.write(config_text)
        handle.close()
        os.chmod(handle.name, 0o600)
        proc = subprocess.run([SSH, "-F", handle.name, "-G", "example-host"],
                              capture_output=True, text=True)
        stderr = proc.stderr or ""
        for line in stderr.splitlines():
            if any(marker in line for marker in _PARSE_COMPLAINTS):
                return stderr.strip()
        return None
    finally:
        os.unlink(handle.name)


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
    """Judged by whichever OpenSSH is installed -- on Windows CI, Windows' own."""

    def test_the_reported_config_really_is_rejected(self):
        """Guards the premise: this is the failure the user hit."""
        error = ssh_config_error(USER_BLOCK + BROKEN_BLOCK)
        self.assertIsNotNone(error)
        self.assertIn("extra arguments", error)

    def test_pruning_makes_it_parseable_again(self):
        cleaned, _ = prune_broken_blocks(USER_BLOCK + BROKEN_BLOCK + GOOD_BLOCK)
        self.assertIsNone(ssh_config_error(cleaned))

    def test_a_windows_path_with_a_space_parses(self):
        self.assertIsNone(ssh_config_error(GOOD_BLOCK))

    def test_unquoted_windows_path_with_a_space_would_have_broken_it(self):
        """Why IdentityFile is quoted: the unquoted form is a latent Windows bug."""
        error = ssh_config_error(GOOD_BLOCK.replace('"', ''))
        self.assertIsNotNone(error)
        self.assertIn("extra arguments", error)


class TestWriterRefusesBadInput(unittest.TestCase):
    """End-to-end through the real writer, with a home directory whose name has a
    space -- so the quoting fix is exercised on every platform, and on Windows CI
    against a genuine `C:\\...\\Jo Smith\\...` path."""

    def setUp(self):
        self._saved_env = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE")}
        self._tmp = tempfile.mkdtemp()
        self.home = os.path.join(self._tmp, "Jo Smith")
        os.makedirs(os.path.join(self.home, ".ssh"))
        # ntpath.expanduser reads USERPROFILE, posixpath reads HOME. Set both so the
        # test drives the same code path on every OS.
        os.environ["HOME"] = self.home
        os.environ["USERPROFILE"] = self.home
        self.config = os.path.join(self.home, ".ssh", "config")
        self.manager = VSCodeManager(
            hostname="hpc3.rcic.uci.edu", username="jinx19",
            key_path=os.path.join(self.home, ".ssh", "jinx19_hpc_app_key"))

    def tearDown(self):
        # Restore BOTH, and unset any we invented -- leaking USERPROFILE would
        # corrupt every later test on Windows.
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self._tmp, ignore_errors=True)

    def read(self):
        if not os.path.exists(self.config):
            return ""
        with open(self.config) as handle:
            return handle.read()

    def assertConfigParses(self):
        if SSH:
            self.assertIsNone(ssh_config_error(self.read()))

    def test_none_assigned_is_never_written(self):
        self.manager._add_ssh_config_to_local("55726278",
                                              {"hostname": "None assigned", "port": "22"})
        self.assertNotIn("None assigned", self.read())

    def test_a_real_node_is_written_and_parses(self):
        self.manager._add_ssh_config_to_local("55726259",
                                              {"hostname": "hpc3-gpu-n54-01", "port": "22"})
        self.assertIn("Host hpc3-gpu-n54-01", self.read())
        self.assertConfigParses()

    def test_the_identity_path_with_a_space_is_quoted(self):
        """The latent Windows bug: an unquoted C:\\Users\\Jo Smith\\... is fatal."""
        self.manager._add_ssh_config_to_local("55726259",
                                              {"hostname": "hpc3-gpu-n54-01", "port": "22"})
        text = self.read()
        self.assertIn("Jo Smith", text)
        for line in text.splitlines():
            if line.strip().startswith("IdentityFile"):
                self.assertRegex(line.strip(), r'^IdentityFile "[^"]+"$')
        self.assertConfigParses()

    def test_a_bogus_port_falls_back_to_22(self):
        self.manager._add_ssh_config_to_local("55726260",
                                              {"hostname": "hpc3-gpu-n54-02", "port": None})
        self.assertIn("Port 22", self.read())
        self.assertConfigParses()

    def test_writing_a_new_session_heals_a_config_broken_by_an_older_build(self):
        with open(self.config, "w") as handle:
            handle.write(USER_BLOCK + BROKEN_BLOCK)
        self.manager._add_ssh_config_to_local("55726259",
                                              {"hostname": "hpc3-gpu-n54-01", "port": "22"})
        text = self.read()
        self.assertNotIn("None assigned", text)
        self.assertIn("Host my-own-server", text)
        self.assertIn("Host hpc3-gpu-n54-01", text)
        self.assertConfigParses()

    def test_startup_repair_heals_without_launching_anything(self):
        with open(self.config, "w") as handle:
            handle.write(USER_BLOCK + BROKEN_BLOCK)
        self.assertEqual(self.manager.repair_local_ssh_config(), 1)
        self.assertNotIn("None assigned", self.read())
        self.assertIn("Host my-own-server", self.read())
        self.assertConfigParses()

    def test_repair_is_idempotent_and_leaves_clean_configs_alone(self):
        with open(self.config, "w") as handle:
            handle.write(USER_BLOCK + GOOD_BLOCK)
        before = self.read()
        self.assertEqual(self.manager.repair_local_ssh_config(), 0)
        self.assertEqual(self.read(), before)

    def test_removing_one_session_leaves_the_others(self):
        for job, node in (("1", "hpc3-gpu-n54-01"), ("2", "hpc3-gpu-n54-02")):
            self.manager._add_ssh_config_to_local(job, {"hostname": node, "port": "22"})
        self.manager._remove_ssh_config_from_local("1")
        text = self.read()
        self.assertNotIn("hpc3-gpu-n54-01", text)
        self.assertIn("hpc3-gpu-n54-02", text)
        self.assertConfigParses()


class TestWindowsSubprocessFlags(unittest.TestCase):
    """The --windowed build must not flash a console. CREATE_NO_WINDOW is a real
    Windows flag: if the value were wrong, subprocess would raise at call time --
    which only Windows CI can actually discover."""

    def test_no_window_is_only_populated_on_windows(self):
        self.assertEqual(bool(_NO_WINDOW), ON_WINDOWS)

    def test_a_subprocess_accepts_the_flags(self):
        proc = subprocess.run([sys.executable, "-c", "print('ok')"],
                              capture_output=True, text=True, **_NO_WINDOW)
        self.assertEqual(proc.stdout.strip(), "ok")

    @unittest.skipIf(SSH_KEYGEN is None, "no ssh-keygen available")
    def test_clearing_a_stale_host_key_never_raises(self):
        manager = VSCodeManager(hostname="hpc3.rcic.uci.edu", username="jinx19",
                                key_path=None)
        manager._clear_stale_host_key("hpc3-gpu-n54-01", "22")  # must not raise


class TestCoverageGuards(unittest.TestCase):
    """Fails, rather than skips, when CI is missing the tools it promised to use."""

    @unittest.skipUnless(REQUIRE_SSH, "only enforced when HPC3_REQUIRE_SSH=1")
    def test_a_real_ssh_client_is_present(self):
        self.assertIsNotNone(
            SSH, "no ssh on PATH: every OpenSSH parser test would silently skip")

    @unittest.skipUnless(REQUIRE_SSH, "only enforced when HPC3_REQUIRE_SSH=1")
    def test_ssh_keygen_is_present(self):
        self.assertIsNotNone(
            SSH_KEYGEN, "no ssh-keygen on PATH: the host-key test would skip")


if __name__ == "__main__":
    unittest.main(verbosity=2)
