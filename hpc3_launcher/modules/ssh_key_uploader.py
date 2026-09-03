#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
First-login key bootstrap: generate a keypair, sign in with password + DUO once,
and drop the public key into the cluster's authorized_keys so every later login
is keyless.

Changes from the old version:
  * ed25519 instead of RSA-4096 -- generates instantly and is the modern default
    (RSA-4096 keygen could stall for a second or two on older Macs, with no UI)
  * no bundled test credentials (there were real-looking ones hardcoded here)
  * progress is reported through a ``reporter`` so the UI can show, in particular,
    "Approve the Duo Push on your phone" -- the old code silently auto-sent a push
    and left the user staring at a frozen window wondering what to do
  * failures raise ``SSHError`` with an actionable hint instead of printing + returning False
"""

import os
import subprocess
import argparse
import getpass

import paramiko

from core.ssh_session import HPC_SERVER, SSH_PORT, SSHError, key_path_for, APP_MARKER

# The app is packaged with PyInstaller --windowed; without this every ssh-keygen
# call would flash a console window on Windows.
_NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def generate_and_upload_ssh_key(username, password, host=HPC_SERVER, port=SSH_PORT,
                                key_comment=None, force=True, reporter=None):
    """
    Generate an ed25519 key and upload its public half to ``host``.

    Must be run on a worker thread (it blocks on the network + DUO). Returns True
    on success; raises ``SSHError`` (with ``.hint``) on any failure.
    """
    def say(state, detail=""):
        if reporter:
            reporter.state(state, detail)

    ssh_dir = os.path.expanduser("~/.ssh")
    key_file = key_path_for(username)
    public_key_file = f"{key_file}.pub"

    os.makedirs(ssh_dir, mode=0o700, exist_ok=True)

    # Generate the keypair (passphraseless on purpose -- that's what lets later
    # logins be non-interactive). ed25519 is fast; this is effectively instant.
    say("creating a key", "generating a new ed25519 key for HPC3")
    if os.path.exists(key_file) and not force:
        raise SSHError(f"a key already exists at {key_file}",
                       "Delete the saved user first, or pass force=True.")
    for path in (key_file, public_key_file):
        if os.path.exists(path):
            os.remove(path)
    comment = key_comment or f"{username}{APP_MARKER}"
    try:
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-f", key_file, "-N", "", "-C", comment],
            check=True, capture_output=True, **_NO_WINDOW,
        )
    except FileNotFoundError:
        raise SSHError("ssh-keygen not found",
                       "OpenSSH must be installed. It ships with macOS and Linux; on "
                       "Windows enable Settings > Apps > Optional features > "
                       "OpenSSH Client.")
    except subprocess.CalledProcessError as e:
        raise SSHError(f"ssh-keygen failed: {e.stderr.decode('utf-8', 'replace')}",
                       "Check that ~/.ssh is writable.")
    os.chmod(key_file, 0o600)

    with open(public_key_file, "r") as f:
        public_key = f.read().strip()

    # Authenticate once with password + DUO via keyboard-interactive.
    say("signing in", f"authenticating as {username} on {host}")
    transport = paramiko.Transport((host, port))
    duo_prompted = {"sent": False}

    def duo_handler(_title, _instructions, prompt_list):
        responses = []
        for prompt, _is_secret in prompt_list:
            if "Password:" in prompt:
                responses.append(password)
            elif "Passcode" in prompt or "Duo" in prompt or "passcode" in prompt:
                # Option 1 = send a Duo Push. Tell the user to go approve it --
                # this is the moment the old app left them hanging.
                if not duo_prompted["sent"]:
                    say("waiting for DUO",
                        "Approve the Duo Push on your phone (or tap your security key)")
                    duo_prompted["sent"] = True
                responses.append("1")
            else:
                responses.append("")
        return responses

    try:
        transport.start_client(timeout=15)
        transport.auth_interactive(username, duo_handler)
        if not transport.is_authenticated():
            raise SSHError(
                "authentication failed",
                "Check your UCI password, and approve the Duo Push when it arrives.",
            )
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client._transport = transport
    except SSHError:
        transport.close()
        raise
    except paramiko.AuthenticationException:
        transport.close()
        raise SSHError("authentication failed",
                       "Wrong password, or the Duo Push was denied/timed out. Try again.")
    except Exception as e:
        transport.close()
        raise SSHError(f"could not sign in to {host}: {e}",
                       "Check your connection (and campus VPN if off-campus), then retry.")

    # Append the public key to authorized_keys (idempotent: skip if already there).
    say("uploading key", "installing your key on HPC3 for keyless login")
    try:
        command = (
            "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
            "touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && "
            f'grep -qxF "{public_key}" ~/.ssh/authorized_keys || '
            f'echo "{public_key}" >> ~/.ssh/authorized_keys'
        )
        _stdin, stdout, stderr = client.exec_command(command)
        exit_status = stdout.channel.recv_exit_status()
        if exit_status != 0:
            err = stderr.read().decode("utf-8", "replace")
            raise SSHError(f"could not install the key: {err}",
                           "The cluster home directory may be full or read-only.")
        say("ready", "key installed — future logins won't need a password")
        return True
    finally:
        client.close()


def _cli():
    parser = argparse.ArgumentParser(description="HPC3 Launcher SSH key bootstrap")
    parser.add_argument("-u", "--username", required=True)
    parser.add_argument("-H", "--host", default=HPC_SERVER)
    parser.add_argument("-P", "--port", type=int, default=SSH_PORT)
    parser.add_argument("-c", "--comment")
    args = parser.parse_args()
    password = getpass.getpass("Password: ")
    try:
        generate_and_upload_ssh_key(
            username=args.username, password=password,
            host=args.host, port=args.port, key_comment=args.comment, force=True,
        )
        print("Done.")
    except SSHError as e:
        print(f"Failed: {e}\nHint: {e.hint}")
        raise SystemExit(1)


if __name__ == "__main__":
    _cli()
