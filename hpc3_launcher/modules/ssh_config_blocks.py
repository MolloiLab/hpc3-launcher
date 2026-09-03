#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Arithmetic on the launcher's own blocks in ~/.ssh/config.

Deliberately free of PyQt and of any app state, so this can be unit tested on
its own. It edits a file the user also hand-edits and that every SSH connection
they make depends on, and the failure mode is silent: a wrong block does not
raise, it just sends ssh somewhere unhelpful.
"""

import re
import shlex

# Both markers: the one written today, and the one the older app wrote.
_TAG = r"HPC(?:3 Launcher| App) VSCode(?: Configuration)?"

# One managed block. The job id in END is a backreference to the one in BEGIN, so
# a match can never run from one block's BEGIN to a different block's END.
_BLOCK = re.compile(
    rf"\n*# === BEGIN {_TAG} \(JobID: (?P<jid>[^)]*)\) ===.*?"
    rf"# === END {_TAG} \(JobID: (?P=jid)\) ===",
    re.DOTALL,
)

# The `Host` lines inside a block: one jump host (hpc_login_<jobid>) and one node.
_HOST_LINE = re.compile(r"^Host (\S+)\s*$", re.M)


def node_of_block(block_text):
    """The compute node a managed block is for, or None.

    A block declares two hosts — the per-job jump host and the node itself. The
    node is the one that is not a jump host.
    """
    for host in _HOST_LINE.findall(block_text):
        if not host.startswith("hpc_login_"):
            return host
    return None


def strip_blocks_for_node(config_text, node, keep_job_id=None):
    """Drop every managed block that configures `node`, returning (text, removed).

    A compute node is allocated to one job at a time, so a block naming this node
    that belongs to a *different* job describes an allocation that has ended. Left
    in place those accumulate — and because OpenSSH uses the FIRST value it finds
    for each keyword while new blocks are appended at the end, the oldest, deadest
    block is the one that wins. Connections then run through the jump host of a job
    that finished weeks ago.

    Blocks the user wrote themselves are never touched; only our own markers are.

    `keep_job_id` spares that job's block, for the caller that is about to write a
    fresh one and wants the file to contain exactly one block for this node.
    """
    keep = None if keep_job_id is None else str(keep_job_id)
    removed = 0
    out = []
    last = 0
    for match in _BLOCK.finditer(config_text):
        if node_of_block(match.group(0)) != node:
            continue
        if keep is not None and match.group("jid") == keep:
            continue
        out.append(config_text[last:match.start()])
        last = match.end()
        removed += 1
    if removed == 0:
        return config_text, 0
    out.append(config_text[last:])
    return "".join(out), removed


# The only directives the launcher ever writes. A block containing anything else
# is not purely ours -- most likely a marker went missing and the match ran wide
# -- so it is left alone rather than risk deleting the user's own config.
_OUR_DIRECTIVES = {
    "host", "hostname", "user", "port", "identityfile", "proxyjump",
    "stricthostkeychecking", "userknownhostsfile",
}


def _directive_token_count(line):
    """Tokens in an ssh_config directive, honouring quoted arguments.

    ``posix=False`` keeps Windows backslashes intact, so
    ``IdentityFile "C:\\Users\\Jo Smith\\.ssh\\key"`` counts as two tokens, not four.
    Unbalanced quotes return -1: also broken, also must go.
    """
    try:
        return len(shlex.split(line, posix=False))
    except ValueError:
        return -1


def prune_broken_blocks(config_text):
    """Drop managed blocks OpenSSH would refuse to parse, returning (text, dropped).

    Slurm reports a job with no allocation as the string "None assigned", and an
    older build wrote that straight in as a hostname:

        Host None assigned
            HostName None assigned

    OpenSSH answers "keyword hostname extra arguments at end of line" and then
    TERMINATES -- it abandons the entire file, so every host the user has stops
    resolving, not just ours. Configs like that are already on disk, so it is not
    enough to stop writing them; they have to be cleaned up.

    Every directive we emit is ``Keyword SingleArgument``, so a block of purely our
    keywords containing anything else is ours and is malformed. Deliberately
    conservative in the other direction: a block mentioning a keyword we never write
    is left untouched, because a user's legitimate ``Host dev prod staging`` also has
    three tokens and must never be collateral damage.
    """
    dropped = []

    def _maybe_drop(match):
        block = match.group(0)
        directives = []
        for line in block.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            keyword = stripped.split(None, 1)[0].rstrip("=").lower()
            if keyword not in _OUR_DIRECTIVES:
                return block  # not purely ours -- hands off
            directives.append(stripped)
        for stripped in directives:
            if _directive_token_count(stripped) != 2:
                dropped.append(stripped)
                return ""
        return block

    return _BLOCK.sub(_maybe_drop, config_text), dropped
