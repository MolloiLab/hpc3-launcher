#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Arithmetic on the launcher's own blocks in ~/.ssh/config.

Deliberately free of PyQt and of any app state, so this can be unit tested on
its own. It edits a file the user also hand-edits and that every SSH connection
they make depends on, and the failure mode is silent: a wrong block does not
raise, it just sends ssh somewhere unhelpful.
"""

import re

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
