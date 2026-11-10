#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A read-only picture of HPC3's GPU nodes: who holds each GPU, what is idle, and
what would fit right now.

Everything here is arithmetic on the text Slurm prints -- no PyQt, no paramiko --
so it can be unit tested against captured output. ``ClusterInsights`` at the
bottom is the only part that touches the network, and it only ever runs queries
(``sinfo``, ``squeue``, ``sshare``, ``sbatch --test-only``); nothing in this module
submits, changes or cancels a job.

Why it exists: on HPC3 a paid partition and its free mirror are the *same nodes*
(``gpu32`` and ``free-gpu32`` both cover the RTX6000/L40S machines). ``sinfo``
alone says a GPU is "used"; it takes ``squeue`` to say whether a paid or a free
job holds it. And a GPU that is idle is often still unusable, because the node's
CPU cores are already spoken for -- so "idle" and "would start a job now" are
different numbers, and the second is the one that matters when sizing a request.
"""

import logging
import re
import threading
import time

from modules import hpc3_constraints as hc

logger = logging.getLogger(__name__)

GPU_PARTITIONS = ("gpu", "free-gpu", "gpu32", "free-gpu32")
FREE_PARTITIONS = ("free", "free-gpu", "free-gpu32")

# Wide fixed columns: no field here contains a space, so the output splits cleanly
# on whitespace, and nothing is truncated. (An older probe used ``Gres:14``, which
# cut "gpu:RTX6000:4" short and produced a GPU called "RTX600".)
SINFO_CMD = ('sinfo -h -N -O "NodeList:30,Partition:20,StateLong:20,CPUsState:20,'
             'Memory:14,AllocMem:14,Gres:40,GresUsed:60"')
RUNNING_CMD = ("squeue -h -t R -p " + ",".join(GPU_PARTITIONS) + " -o '%P|%N|%b|%u'")
PENDING_CMD = ("squeue -h -t PD -p " + ",".join(GPU_PARTITIONS) + " -o '%P|%r|%Q|%b|%u'")
SHARE_CMD = "sshare -U -h -P -o Account,FairShare"

_SEP = "@@HPC3-SECTION@@"

# Node states a new job can land on. Everything else (down, drained, reserved,
# maintenance, completing...) is shown but never counted as startable.
_USABLE_STATES = ("idle", "mixed")

# Pending reasons, sorted into what they mean for someone else in the queue.
_COMPETING = ("Priority", "Resources", "None")


# --- small parsers -------------------------------------------------------------

def parse_gres(text):
    """``gpu:RTX6000:4`` -> ("RTX6000", 4); anything without a GPU -> ("", 0)."""
    match = re.search(r"gpu:([A-Za-z0-9_]+):(\d+)", text or "")
    if not match:
        return "", 0
    return match.group(1), int(match.group(2))


def parse_gres_used(text):
    """``gpu:L40S:3(IDX:0-2)`` -> 3. The count is the number, not the index list:
    counting pieces of "0-3" (as an older parser did) reports 2 GPUs for 4."""
    return parse_gres(text)[1]


def parse_job_gpus(tres):
    """GPUs per node a job asked for, from squeue's ``%b``.

    ``gres/gpu:RTX6000:2`` -> 2, ``gres/gpu:1`` -> 1, bare ``gres/gpu`` -> 1,
    ``N/A`` -> 0.
    """
    for part in (tres or "").split(","):
        part = part.strip()
        if not part.startswith(("gres/gpu", "gres:gpu", "gpu")):
            continue
        last = part.split("(", 1)[0].split(":")[-1]
        return int(last) if last.isdigit() else 1
    return 0


def expand_hostlist(text):
    """``hpc3-gpu-k54-[06-08],hpc3-gpu-m54-01`` -> the four node names.

    Handles the one bracket form Slurm prints for these nodes; zero padding is kept.
    """
    names = []
    for chunk in re.findall(r"[^,\[]+(?:\[[^\]]*\])?[^,\[]*", text or ""):
        chunk = chunk.strip()
        if not chunk:
            continue
        match = re.match(r"^(.*?)\[([^\]]+)\](.*)$", chunk)
        if not match:
            names.append(chunk)
            continue
        prefix, body, suffix = match.groups()
        for piece in body.split(","):
            if "-" in piece:
                lo, hi = piece.split("-", 1)
                if lo.isdigit() and hi.isdigit():
                    for n in range(int(lo), int(hi) + 1):
                        names.append(f"{prefix}{str(n).zfill(len(lo))}{suffix}")
                    continue
            names.append(f"{prefix}{piece}{suffix}")
    return names


def base_state(state):
    """``mixed-`` / ``down*`` / ``idle~`` -> the bare state word."""
    return re.sub(r"[^a-z+]", "", (state or "").lower()).split("+")[0]


# --- nodes ---------------------------------------------------------------------

def parse_nodes(output):
    """Parse ``SINFO_CMD`` output into {node: info}, one entry per node.

    A node appears once per partition it belongs to; those rows are merged and the
    partitions collected, which is what lets the view say "this node takes free
    jobs" (hpc3-gpu-k54-00, for one, is in ``free-gpu`` but not ``gpu``).
    """
    nodes = {}
    for line in (output or "").splitlines():
        parts = line.split()
        if len(parts) < 8:
            continue
        name, partition, state, cpus, memory, alloc_mem, gres, gres_used = parts[:8]
        cpu = re.match(r"^(\d+)/(\d+)/(\d+)/(\d+)$", cpus)
        if not cpu or not memory.isdigit():
            continue
        partition = partition.rstrip("*")
        node = nodes.get(name)
        if node is None:
            model, total = parse_gres(gres)
            node = nodes[name] = {
                "name": name,
                "partitions": [],
                "state": state,
                "cpus_alloc": int(cpu.group(1)),
                "cpus_idle": int(cpu.group(2)),
                "cpus_other": int(cpu.group(3)),
                "cpus_total": int(cpu.group(4)),
                "mem_mb": int(memory),
                "alloc_mem_mb": int(alloc_mem) if alloc_mem.isdigit() else 0,
                "gpu_model": model,
                "gpu_total": total,
                "gpu_used": min(parse_gres_used(gres_used), total) if total else 0,
                "gpu_paid": 0,
                "gpu_free": 0,
            }
        if partition not in node["partitions"]:
            node["partitions"].append(partition)
    return nodes


def parse_running(output):
    """Parse ``RUNNING_CMD`` output into [{partition, nodes, gpus, user}]."""
    jobs = []
    for line in (output or "").splitlines():
        parts = line.strip().split("|")
        if len(parts) < 4:
            continue
        gpus = parse_job_gpus(parts[2])
        if not gpus:
            continue
        jobs.append({"partition": parts[0], "nodes": expand_hostlist(parts[1]),
                     "gpus": gpus, "user": parts[3]})
    return jobs


def attribute_gpus(nodes, running, username=None):
    """Split each node's used GPUs into paid and free, in place.

    ``gpu_free`` is what free-partition jobs hold; ``gpu_paid`` is the rest of what
    is in use. Deriving paid as the remainder keeps the three segments (paid, free,
    idle) summing to the node's GPU count even when a job is on a partition this
    module doesn't know about. ``gpu_mine`` counts the user's own GPUs.
    """
    for node in nodes.values():
        node["gpu_free"] = node["gpu_mine"] = 0
    for job in running:
        for name in job["nodes"]:
            node = nodes.get(name)
            if node is None:
                continue
            if job["partition"] in FREE_PARTITIONS:
                node["gpu_free"] += job["gpus"]
            if username and job["user"] == username:
                node["gpu_mine"] += job["gpus"]
    for node in nodes.values():
        node["gpu_free"] = min(node["gpu_free"], node["gpu_used"])
        node["gpu_paid"] = node["gpu_used"] - node["gpu_free"]
        node["gpu_mine"] = min(node["gpu_mine"], node["gpu_used"])
    return nodes


def node_family(node):
    """Partition family ("gpu" / "gpu32" / "cpu") a node belongs to."""
    model = node.get("gpu_model")
    if model in hc.GPU_MODELS:
        return hc.GPU_MODELS[model]["family"]
    for partition in node.get("partitions", []):
        if partition.endswith("gpu32"):
            return "gpu32"
        if partition.endswith("gpu"):
            return "gpu"
    return "cpu"


def takes_free_jobs(node):
    return any(p in FREE_PARTITIONS for p in node.get("partitions", []))


def room(node, use_free=True):
    """What one new job could get on this node right now.

    Returns {gpus, cpus, mem_gb}: the idle GPUs, the idle cores, and the most memory
    a job could be given -- bounded both by what the node has left and by the
    partition's memory-per-core cap applied to those idle cores. All zeros when the
    node can't start anything (wrong state, no idle GPU, fewer idle cores than a GPU
    job's two-core minimum, or no memory left).
    """
    empty = {"gpus": 0, "cpus": 0, "mem_gb": 0}
    if base_state(node.get("state")) not in _USABLE_STATES:
        return empty
    if use_free and not takes_free_jobs(node):
        return empty
    gpus = node["gpu_total"] - node["gpu_used"]
    cpus = node["cpus_idle"]
    if gpus <= 0 or cpus < hc.DEF_CPU_PER_GPU:
        return empty
    gpus = min(gpus, cpus // hc.DEF_CPU_PER_GPU)
    spec = hc.FAMILIES[node_family(node)]
    per_core = hc.MEM_PER_CPU_GB.get(spec["free" if use_free else "paid"], 6)
    left_gb = max(0, node["mem_mb"] - node["alloc_mem_mb"]) // 1024
    mem_gb = int(min(left_gb, cpus * per_core))
    if mem_gb < 1:      # idle GPU and cores, but every GB is already promised
        return empty
    return {"gpus": gpus, "cpus": cpus, "mem_gb": mem_gb}


def request_fits(node, gpus, cpus, mem_gb, use_free=True):
    """Would a job asking for this much start on this node right now?"""
    have = room(node, use_free)
    return have["gpus"] >= gpus and have["cpus"] >= cpus and have["mem_gb"] >= mem_gb


def nodes_for(nodes, family=None, gpu_type=None):
    """The GPU nodes a request could land on, by family and/or exact model."""
    picked = []
    for node in nodes.values():
        if not node["gpu_total"]:
            continue
        if gpu_type and node["gpu_model"] != gpu_type:
            continue
        if family and family != "anygpu" and node_family(node) != family:
            continue
        picked.append(node)
    return picked


def fit_report(nodes, family, gpu_type, gpus, cpus, mem_gb, use_free=True):
    """How a request compares with what is idle right now.

    Returns {fits: [node names], idle_gpus, best: room-dict-with-name or None}.
    ``best`` is the roomiest node that has at least the requested GPU count, which
    is what "shrink to fit" sizes the request down to.
    """
    candidates = nodes_for(nodes, family, gpu_type or None)
    fits, best, idle = [], None, 0
    for node in candidates:
        have = room(node, use_free)
        idle += node["gpu_total"] - node["gpu_used"]
        if have["gpus"] >= gpus and have["cpus"] >= cpus and have["mem_gb"] >= mem_gb:
            fits.append(node["name"])
        if have["gpus"] >= gpus:
            key = (have["mem_gb"], have["cpus"])
            if best is None or key > (best["mem_gb"], best["cpus"]):
                best = dict(have, name=node["name"])
    return {"fits": sorted(fits), "idle_gpus": idle, "best": best}


def summarize_models(nodes, use_free=True):
    """Per GPU model: total / paid / free / idle GPUs, and how many could start a
    job right now. Ordered as the launcher lists them."""
    order = [m for fam in ("gpu32", "gpu") for m in hc._FAMILY_MODEL_ORDER[fam]]
    rows = {}
    for node in nodes.values():
        model = node["gpu_model"]
        if not node["gpu_total"] or not model:
            continue
        row = rows.setdefault(model, {
            "model": model, "family": node_family(node),
            "vram_gb": hc.GPU_MODELS.get(model, {}).get("vram_gb"),
            "nodes": 0, "total": 0, "paid": 0, "free": 0, "idle": 0, "startable": 0,
        })
        row["nodes"] += 1
        row["total"] += node["gpu_total"]
        row["paid"] += node["gpu_paid"]
        row["free"] += node["gpu_free"]
        row["idle"] += node["gpu_total"] - node["gpu_used"]
        row["startable"] += room(node, use_free)["gpus"]
    return sorted(rows.values(),
                  key=lambda r: (order.index(r["model"]) if r["model"] in order else 99,
                                 r["model"]))


# --- the queue -----------------------------------------------------------------

def parse_pending(output):
    """Parse ``PENDING_CMD`` output into [{partitions, reason, priority, gpus, user}]."""
    jobs = []
    for line in (output or "").splitlines():
        parts = line.strip().split("|")
        if len(parts) < 5 or not parts[2].isdigit():
            continue
        jobs.append({"partitions": [p for p in parts[0].split(",") if p],
                     "reason": parts[1], "priority": int(parts[2]),
                     "gpus": parse_job_gpus(parts[3]), "user": parts[4]})
    return jobs


def queue_summary(pending):
    """Per partition: how many jobs wait, and how many of those are truly in line.

    A job blocked by its own limits (an array throttle, a per-account GPU cap, a
    dependency, a hold) is not competing for the next free GPU, so it is counted
    apart from the ones waiting on priority or resources.
    """
    rows = {}
    for job in pending:
        competing = job["reason"] in _COMPETING
        for partition in job["partitions"]:
            row = rows.setdefault(partition, {"partition": partition, "pending": 0,
                                              "competing": 0, "blocked": 0,
                                              "top_priority": 0})
            row["pending"] += 1
            row["competing" if competing else "blocked"] += 1
            if competing:
                row["top_priority"] = max(row["top_priority"], job["priority"])
    return [rows[p] for p in GPU_PARTITIONS if p in rows]


def jobs_ahead(pending, partition, priority):
    """Competing jobs in ``partition`` that outrank ``priority``; and how many compete."""
    competing = [j for j in pending
                 if partition in j["partitions"] and j["reason"] in _COMPETING]
    return sum(1 for j in competing if j["priority"] > priority), len(competing)


# --- priority ------------------------------------------------------------------

# HPC3's multifactor weights (``sprio -w``): fair share dominates. Free partitions
# carry no partition priority, so a free job's priority is, to within the small
# age term, 2000 x the fair-share factor of the account it is submitted under,
# plus a constant ~110 for job size and QOS.
FAIRSHARE_WEIGHT = 2000
FREE_BASE_PRIORITY = 110


def parse_fairshare(output):
    """Parse ``SHARE_CMD`` output into {account: fair-share factor (0..1)}."""
    shares = {}
    for line in (output or "").splitlines():
        parts = line.strip().split("|")
        if len(parts) < 2:
            continue
        try:
            shares[parts[0].strip()] = float(parts[1])
        except ValueError:
            continue
    return shares


def free_priority(fairshare):
    """The queue priority a free job gets under an account with this fair share."""
    return int(round(FAIRSHARE_WEIGHT * fairshare)) + FREE_BASE_PRIORITY


def best_free_account(accounts, shares):
    """The account (from ``accounts``) with the highest fair share, or None."""
    known = [a for a in accounts if a in shares]
    if not known:
        return None
    return max(known, key=lambda a: (shares[a], a))


def parse_test_only(output):
    """Read ``sbatch --test-only``'s verdict.

    Returns {ok, start, node, message}: on success ``start`` is the ISO time Slurm
    expects the job to begin; otherwise ``message`` is Slurm's complaint.
    """
    text = (output or "").strip()
    match = re.search(r"to start at (\S+)(?:.*? on nodes (\S+))?", text)
    if match:
        return {"ok": True, "start": match.group(1), "node": match.group(2) or "",
                "message": ""}
    lines = [re.sub(r"^(sbatch|srun|salloc):\s*(error:\s*)?", "", line).strip()
             for line in text.splitlines() if line.strip()]
    return {"ok": False, "start": "", "node": "",
            "message": " ".join(lines) or "Slurm gave no estimate"}


# --- the one part that talks to the cluster ------------------------------------

class ClusterInsights:
    """Fetches the snapshot above over one keyed SSH session.

    Read-only by construction: the four commands it runs are queries. Call it from
    a worker thread; a lock serialises callers and a short cache lets two pages
    ask for the same picture without two round trips.
    """

    CACHE_SECONDS = 20

    def __init__(self, session, username):
        self._session = session
        self.username = username
        self._lock = threading.Lock()
        self._cached = None
        self._cached_at = 0.0

    def snapshot(self, reporter=None, force=False):
        with self._lock:
            if (not force and self._cached is not None
                    and time.time() - self._cached_at < self.CACHE_SECONDS):
                return self._cached
            if reporter:
                reporter.state("loading", "reading GPU nodes and the queue")
            command = f"; echo '{_SEP}'; ".join(
                [SINFO_CMD, RUNNING_CMD, PENDING_CMD, SHARE_CMD])
            output = self._session.run(command, reporter)
            self._cached = build_snapshot(output, self.username)
            self._cached_at = time.time()
            return self._cached

    def estimate_start(self, sbatch_command, reporter=None):
        """Ask Slurm when a job would start, without submitting it."""
        if " --test-only" not in sbatch_command:
            raise ValueError("estimate_start only runs sbatch --test-only")
        with self._lock:
            return parse_test_only(self._session.run(sbatch_command + " 2>&1", reporter))


def build_snapshot(output, username=None):
    """Turn the combined query output into one dict the UI can render."""
    sections = (output or "").split(_SEP)
    sections += [""] * (4 - len(sections))
    nodes = parse_nodes(sections[0])
    attribute_gpus(nodes, parse_running(sections[1]), username)
    pending = parse_pending(sections[2])
    return {
        "nodes": nodes,
        "models": summarize_models(nodes),
        "pending": pending,
        "queue": queue_summary(pending),
        "fairshare": parse_fairshare(sections[3]),
        "taken_at": time.time(),
    }
