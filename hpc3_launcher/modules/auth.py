#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Authentication + key discovery for HPC3 Launcher.

Constants live in ``core.ssh_session`` now; this module re-exports the ones the
rest of the app imports (HPC_SERVER, APP_MARKER) so existing imports keep working.

The two big behaviour fixes here:
  * connectivity is checked with a TCP probe to port 22, not ICMP ping. HPC
    firewalls commonly drop ping, which made the old check report "server
    unreachable" even when SSH was perfectly fine.
  * key login no longer blindly connects to the cluster once per saved key. It
    tries the relevant key (the one you picked, or the most-recently-used) and
    fails fast with a hint -- the old loop could mean 5s of frozen UI per stale key.

Everything that touches the network is meant to run inside an ``SSHWorker``.
"""

import os
import logging

from core.ssh_session import (
    HPC_SERVER, SSH_PORT, APP_MARKER, SSH_DIR, key_path_for,
    SSHError, probe_host, HPCSession,
)
from modules.ssh_key_uploader import generate_and_upload_ssh_key  # re-exported for callers

logger = logging.getLogger(__name__)

# Last successful node info, for callers that want to show it post-login.
LAST_NODE_INFO = None


# --- saved users ---------------------------------------------------------------

def get_all_existing_users():
    """Saved users (anything with a ``*_hpc_app_key`` private key), newest first."""
    users = []
    if not os.path.exists(SSH_DIR):
        return users
    for file in os.listdir(SSH_DIR):
        if file.endswith(APP_MARKER) and not file.endswith(".pub"):
            username = file[: -len(APP_MARKER)]
            key_path = os.path.join(SSH_DIR, file)
            if os.access(key_path, os.R_OK) and os.path.exists(f"{key_path}.pub"):
                users.append({
                    "username": username,
                    "key_path": key_path,
                    "last_used": os.path.getmtime(key_path),
                })
    users.sort(key=lambda u: u["last_used"], reverse=True)
    return users


def delete_user_key(username):
    """Remove a saved user's local keypair. Returns True on success."""
    try:
        key_path = key_path_for(username)
        for path in (key_path, f"{key_path}.pub"):
            if os.path.exists(path):
                os.remove(path)
                logger.info("deleted %s", path)
        return True
    except OSError as e:
        logger.error("error deleting key for %s: %s", username, e)
        return False


# --- reachability --------------------------------------------------------------

def check_network_connectivity(host=HPC_SERVER):
    """True if HPC3's SSH port answers. TCP probe, not ping (ping is often blocked)."""
    ok, detail = probe_host(host)
    (logger.info if ok else logger.warning)("reachability %s: %s", host, detail)
    return ok


def can_connect_to_hpc():
    return check_network_connectivity(HPC_SERVER)


# --- node info -----------------------------------------------------------------

def get_node_info_via_key(username, reporter=None):
    """
    Fetch a short hostname/node summary over the keyed session.

    Returns a human string. Raises ``SSHError`` on failure (run inside a worker).
    """
    session = HPCSession(username)
    try:
        if reporter:
            reporter.state("checking", "reading your HPC3 node info")
        hostname = session.run("hostname", reporter).strip()
        node_info = session.run("sinfo -N | grep $(hostname) || true", reporter).strip()
        return f"Hostname: {hostname}\nNode Info: {node_info}"
    finally:
        session.close()


# --- key login -----------------------------------------------------------------

def check_and_login_with_key(specific_username=None, reporter=None):
    """
    Verify a saved key actually works against HPC3.

    Returns ``(success, username, error_message)``. Prefers the requested user (or
    the most-recently-used saved key) and verifies just that one with a single
    fast keyed connect -- no looping over every stale key on the UI thread.
    """
    users = get_all_existing_users()
    if not users:
        return False, None, "No saved key found. Log in with your password to create one."

    if specific_username:
        candidate = next((u for u in users if u["username"] == specific_username), None)
        if candidate is None:
            return False, None, f"No saved key for {specific_username}."
        order = [candidate]
    else:
        order = users  # newest first

    last_error = None
    for user in order:
        username = user["username"]
        try:
            if reporter:
                reporter.state("connecting", f"verifying {username}'s key on {HPC_SERVER}")
            session = HPCSession(username, key_path=user["key_path"])
            try:
                session.run("true", reporter)  # cheap round-trip == key works
            finally:
                session.close()
            logger.info("verified key for %s", username)
            return True, username, None
        except SSHError as e:
            logger.warning("key for %s did not work: %s", username, e)
            last_error = e
            # If the user explicitly chose this account, surface the real reason now.
            if specific_username:
                return False, username, f"{e} — {e.hint}"
            continue
    hint = getattr(last_error, "hint", "") if last_error else ""
    return False, None, ("No saved key worked. " + hint).strip()


# --- backward-compatible helpers (kept for any external callers) ---------------

def get_last_node_info():
    return LAST_NODE_INFO


def login_with_password(uc_id, password, duo_code=None, reporter=None):
    """Create+upload a key, then return ``(success, node_info)``. Legacy shim."""
    global LAST_NODE_INFO
    try:
        generate_and_upload_ssh_key(username=uc_id, password=password,
                                    host=HPC_SERVER, force=True, reporter=reporter)
        LAST_NODE_INFO = get_node_info_via_key(uc_id, reporter)
        return True, LAST_NODE_INFO
    except SSHError as e:
        logger.error("password login failed: %s", e)
        return False, None


def verify_credentials(uc_id, password, duo_code=None):
    ok, _ = login_with_password(uc_id, password, duo_code)
    return ok
