#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Central SSH plumbing for HPC3 Launcher.

The whole point of this module is that *nothing* here is ever supposed to run on
the Qt UI thread. Connecting to HPC3 can take many seconds (DNS, the login-node
banner, DUO, a slow link), and every one of those seconds is a frozen window if
it happens on the main thread. So callers wrap their work in an ``SSHWorker`` and
get back a live stream of ``state``/``detail`` updates plus a ``log`` channel,
modelled on PlutoSpace's remote-session opener.

Design notes:
  * keyed SSH only for everything after the first login (no password kept around)
  * short, explicit timeouts so an unreachable host fails fast instead of hanging
  * a TCP probe (not ICMP ping) for reachability -- HPC firewalls routinely drop
    ping while leaving 22 open, which is why the old ``ping`` check gave false
    "server unreachable" errors
  * errors carry a human ``hint`` so the UI can say *what to do*, not just "failed"
"""

import os
import socket
import logging

import paramiko
from PyQt5.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)

# --- Cluster + key conventions -------------------------------------------------

HPC_SERVER = "hpc3.rcic.uci.edu"
SSH_PORT = 22

# Key naming is kept identical to the old app on purpose: existing users already
# have ``<username>_hpc_app_key`` in ~/.ssh and re-keying means another DUO dance.
# Renaming the marker would silently orphan every current login.
APP_MARKER = "_hpc_app_key"
SSH_DIR = os.path.expanduser("~/.ssh")

# Fast-fail timeouts (seconds). PlutoSpace uses ConnectTimeout=8; we match it and
# add explicit banner/auth budgets so a wedged login node can't hang us forever.
CONNECT_TIMEOUT = 8
BANNER_TIMEOUT = 12
AUTH_TIMEOUT = 12
PROBE_TIMEOUT = 5
DEFAULT_EXEC_TIMEOUT = 30


def key_path_for(username):
    """Absolute path of the app's private key for a user (may not exist yet)."""
    return os.path.join(SSH_DIR, f"{username}{APP_MARKER}")


# --- Errors that know what the user should do about them -----------------------

class SSHError(Exception):
    """An SSH/remote failure that carries an actionable ``hint`` for the UI."""

    def __init__(self, message, hint=""):
        super().__init__(message)
        self.hint = hint


def classify_error(message):
    """
    Turn a raw exception string into a short, actionable hint.

    Mirrors PlutoSpace's "say what to do, not just that it broke" approach -- the
    raw paramiko/socket text is kept as the message, this is the friendly nudge.
    """
    m = (message or "").lower()
    if "authentication" in m or "auth failed" in m or "not authenticated" in m:
        return ("HPC3 rejected the key. The key may not be on the cluster yet -- "
                "log in again with your password + DUO to (re)upload it.")
    if "no route to host" in m or "network is unreachable" in m or "unreachable" in m:
        return ("Can't reach HPC3. Check your internet, and the campus VPN if you're "
                "off-campus.")
    if "timed out" in m or "timeout" in m:
        return ("HPC3 didn't answer in time. It may be busy or your connection is slow "
                "-- try again, and check the campus VPN if you're off-campus.")
    if "name or service not known" in m or "getaddrinfo" in m or "resolve" in m:
        return ("Couldn't resolve hpc3.rcic.uci.edu -- this is almost always DNS/VPN. "
                "Connect to the campus VPN and retry.")
    if "connection refused" in m:
        return "HPC3 refused the connection on port 22. The cluster may be down for maintenance."
    if "permission denied" in m:
        return ("The cluster refused the key (permission denied). Re-upload it by logging "
                "in with your password + DUO.")
    return "Try again; if it keeps failing, check the campus VPN and that HPC3 is up."


def probe_host(host=HPC_SERVER, port=SSH_PORT, timeout=PROBE_TIMEOUT):
    """
    Is the SSH port actually reachable? A plain TCP connect -- no ICMP, so it works
    even when the firewall drops ping. Returns (ok, detail).
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"{host}:{port} is reachable"
    except socket.gaierror as e:
        return False, (f"can't resolve {host} ({e}) -- this is usually DNS/VPN")
    except (socket.timeout, TimeoutError):
        return False, f"{host}:{port} did not answer within {timeout}s"
    except OSError as e:
        return False, f"can't reach {host}:{port} ({e})"


def connect_with_key(username, key_path, host=HPC_SERVER, port=SSH_PORT):
    """
    Open a keyed paramiko connection (no password, no agent, no key-hunting).

    Raises ``SSHError`` with a hint on failure. Must be called from a worker thread.
    """
    if not key_path or not os.path.exists(key_path):
        raise SSHError(
            f"no SSH key found for {username}",
            "Log in with your password + DUO to create one.",
        )
    client = paramiko.SSHClient()
    # We only ever talk to one well-known host; auto-add keeps first-connect from
    # erroring. (The real security fix is elsewhere: we no longer scribble
    # "StrictHostKeyChecking no" into the user's own ~/.ssh/config.)
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=host,
            port=port,
            username=username,
            key_filename=key_path,
            look_for_keys=False,
            allow_agent=False,
            timeout=CONNECT_TIMEOUT,
            banner_timeout=BANNER_TIMEOUT,
            auth_timeout=AUTH_TIMEOUT,
        )
    except paramiko.AuthenticationException as e:
        raise SSHError(f"key authentication failed: {e}", classify_error("authentication"))
    except (paramiko.SSHException, OSError, socket.error) as e:
        raise SSHError(f"connection to {host} failed: {e}", classify_error(str(e)))
    return client


# --- A pooled, key-based session -----------------------------------------------

class HPCSession:
    """
    A reusable keyed connection to HPC3 for one user.

    Caches a single paramiko client and re-opens it transparently if the transport
    has dropped. Not safe for *concurrent* use from multiple threads, but fine for
    the one-worker-at-a-time pattern the UI uses (callers serialise via SSHWorker).
    All methods must run on a worker thread, never the UI thread.
    """

    def __init__(self, username, key_path=None, host=HPC_SERVER):
        self.username = username
        self.host = host
        self.key_path = key_path or key_path_for(username)
        self._client = None

    def _alive(self):
        t = self._client.get_transport() if self._client else None
        return bool(t and t.is_active())

    def client(self, reporter=None):
        if self._alive():
            return self._client
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        if reporter:
            reporter.state("connecting", f"reaching {self.host} with your SSH key")
        self._client = connect_with_key(self.username, self.key_path, self.host)
        logger.info("opened SSH session to %s as %s", self.host, self.username)
        return self._client

    def run(self, command, reporter=None, timeout=DEFAULT_EXEC_TIMEOUT):
        """Run one command, return stdout. Raises SSHError on transport failure."""
        client = self.client(reporter)
        if reporter:
            reporter.log(f"$ {command}")
        try:
            _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
            out = stdout.read().decode("utf-8", "replace")
            err = stderr.read().decode("utf-8", "replace")
        except (paramiko.SSHException, OSError, socket.error) as e:
            # Drop the dead client so the next call reconnects cleanly.
            self.close()
            raise SSHError(f"command failed on {self.host}: {e}", classify_error(str(e)))
        if err.strip() and reporter:
            for line in err.strip().splitlines():
                reporter.log(line)
        return out

    def close(self):
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None


# --- The background worker every slow op goes through --------------------------

class Reporter:
    """
    Handed to a task function so it can narrate progress without knowing about Qt.

    ``state(name, detail)`` drives the spinner/label; ``log(line)`` appends to the
    expandable log pane. Both hop to the UI thread via queued signals, so calling
    them from the worker thread is safe.
    """

    def __init__(self, worker):
        self._w = worker

    def state(self, state, detail=""):
        self._w.progress.emit(str(state), str(detail))

    def log(self, line):
        self._w.log.emit(str(line).rstrip("\n"))


class SSHWorker(QThread):
    """
    Runs a callable on a background thread and reports progress to the UI.

    The callable receives a single ``Reporter`` argument and returns any result
    object (delivered via ``succeeded``). Raising ``SSHError`` -- or anything else
    -- is turned into a ``failed(message, hint)`` signal; the UI never sees an
    unhandled exception and never freezes waiting on the work.

    Usage:
        self._worker = SSHWorker(lambda rep: do_slow_thing(rep), label="login")
        self._worker.progress.connect(status_view.set_state)
        self._worker.log.connect(status_view.append_log)
        self._worker.succeeded.connect(self._on_done)
        self._worker.failed.connect(status_view.finish_error)
        self._worker.start()

    Keep a reference to the worker (e.g. ``self._worker``) until it finishes, or Qt
    will garbage-collect it mid-run.
    """

    progress = pyqtSignal(str, str)   # state, detail
    log = pyqtSignal(str)             # one log line
    succeeded = pyqtSignal(object)    # result
    failed = pyqtSignal(str, str)     # message, hint

    # Self-retention: a QThread that gets garbage-collected while its underlying
    # thread is still running aborts the whole process ("QThread: Destroyed while
    # thread is still running"). We hold a strong reference to every live worker
    # here and drop it only once `finished` fires (delivered on the owning/UI
    # thread, by which point the thread has stopped), so callers can't crash us by
    # forgetting to keep a reference.
    _live = set()

    def __init__(self, fn, parent=None, label="task"):
        super().__init__(parent)
        self._fn = fn
        self.label = label
        SSHWorker._live.add(self)
        self.finished.connect(self._on_finished)

    def _on_finished(self):
        # Drop our strong ref now that the thread has stopped. Any caller-held
        # reference (e.g. self._worker) keeps the *finished* object alive until it
        # reassigns -- deleting a finished QThread via normal GC is safe; deleting a
        # running one is what aborts. We deliberately do NOT deleteLater() here,
        # because a widget attribute may still point at it.
        SSHWorker._live.discard(self)

    def run(self):
        rep = Reporter(self)
        try:
            result = self._fn(rep)
        except SSHError as e:
            logger.warning("[%s] %s", self.label, e)
            self.failed.emit(str(e), e.hint or "")
            return
        except Exception as e:  # noqa: BLE001 - the UI must never see a raw traceback
            logger.exception("[%s] crashed", self.label)
            self.failed.emit(str(e), classify_error(str(e)))
            return
        self.succeeded.emit(result)
