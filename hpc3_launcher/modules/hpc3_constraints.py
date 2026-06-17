#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""HPC3 GPU/partition constraint model -- the single source of truth for which
job configurations are *valid* on UCI's HPC3 cluster.

The VSCode launcher uses this to make only valid combinations selectable, so a
user can never build a request SLURM will reject with "Requested node
configuration is not available" (which is what happens when, e.g., an RTX6000
GPU is requested on the `gpu` partition instead of `gpu32`).

The dependency chain enforced here:

    account suffix  ->  partition family  ->  legal GPU models
                                          ->  per-node GPU/CPU ceilings
                                          ->  memory cap (GB-per-core)
    use-free flag   ->  paid vs free partition + run-time cap

This is a curated table, not live discovery: the rules below (per-node limits,
9 GB/core, account->partition suffix) are documented policy, not easily/robustly
discoverable from a single SLURM query. Keep it in sync with the RCIC docs.

Source: UCI RCIC docs (https://rcic.uci.edu -- slurm/slurm.html, hpc3/specs.html,
about/allocations.html, help/faq.html), verified 2026-06. Update this file if
HPC3 changes its GPU hardware, partitions, or per-core memory policy.
"""

import math

# GPU model -> partition family + per-node ceilings.
#   family   : which partition family this GPU lives in
#   max_gpus : most GPUs of this type on a single node (max --gres count)
#   max_cpus : CPU cores on that node type (max --cpus-per-task)
GPU_MODELS = {
    "V100":    {"family": "gpu",   "max_gpus": 4, "max_cpus": 40},
    "A30":     {"family": "gpu",   "max_gpus": 4, "max_cpus": 32},
    "A100":    {"family": "gpu",   "max_gpus": 2, "max_cpus": 32},
    "L40S":    {"family": "gpu32", "max_gpus": 4, "max_cpus": 48},
    "RTX6000": {"family": "gpu32", "max_gpus": 4, "max_cpus": 32},
}

# Display order of GPU models within each family (controls dropdown order).
_FAMILY_MODEL_ORDER = {
    "gpu":   ["V100", "A30", "A100"],
    "gpu32": ["L40S", "RTX6000"],
    "cpu":   [],
}

# Partition family -> partition names + the largest CPU-core count across nodes
# in the family (the ceiling for "Any GPU"). paid/free are the SLURM partitions.
FAMILIES = {
    "gpu":   {"paid": "gpu",      "free": "free-gpu",   "node_cpus": 40},
    "gpu32": {"paid": "gpu32",    "free": "free-gpu32", "node_cpus": 48},
    "cpu":   {"paid": "standard", "free": "free",       "node_cpus": 64},
}

# MaxMemPerCPU (GB) PER PARTITION -- the hard cap SLURM enforces. Request more
# memory than (this * cpus) and SLURM silently raises --cpus-per-task to fit, so
# the launcher derives CPUs from memory using these exact numbers. Note it is
# partition-specific (gpu32 is 6, not 9; the free CPU queue is higher than paid).
# Verified via `scontrol show partition` on HPC3, 2026-06.
MEM_PER_CPU_GB = {
    "gpu": 9, "free-gpu": 9,
    "gpu32": 6, "free-gpu32": 6,
    "standard": 6, "free": 18,
}

# HPC3 GPU partitions default to 2 cores per GPU (DefCpuPerGPU) -- used as a
# sensible CPU floor for GPU jobs so a GPU never gets just 1 core.
DEF_CPU_PER_GPU = 2

# Max wall time (hours) by tier: free partitions cap at 3 days, paid at 14 days.
RUNTIME_MAX_HOURS = {"free": 72, "paid": 336}

# Sentinels used throughout the UI for the GPU-type field:
#   None  -> no GPU (CPU-only job)
#   ""    -> any GPU of the account's family
#   "X"   -> a specific model name from GPU_MODELS
GPU_NONE = None
GPU_ANY = ""


def account_family(account):
    """Map an account name to its partition family via its suffix.

    HPC3 rule: to submit to `gpu` you need an account ending in `gpu`; to submit
    to `gpu32` you need one ending in `gpu32`; anything else is CPU-only.
    """
    a = (account or "").lower()
    if a.endswith("gpu32"):
        return "gpu32"
    if a.endswith("gpu"):
        return "gpu"
    return "cpu"


def is_gpu_family(family):
    return family in ("gpu", "gpu32")


def family_gpu_options(family):
    """(label, value) pairs for the GPU-type dropdown, given a partition family.

    CPU families offer only "No GPU"; GPU families offer "Any" plus each model
    that actually lives in that family -- so RTX6000 can never appear under a
    `gpu` account, V100 never under a `gpu32` account, etc.
    """
    if not is_gpu_family(family):
        return [("No GPU", GPU_NONE)]
    options = [("Any GPU (recommended)", GPU_ANY)]
    for model in _FAMILY_MODEL_ORDER.get(family, []):
        options.append((f"{model} GPU (specific)", model))
    return options


def max_gpus_for(gpu_type, family):
    """Most GPUs requestable for the chosen type (per-node ceiling)."""
    if gpu_type in GPU_MODELS:
        return GPU_MODELS[gpu_type]["max_gpus"]
    if gpu_type == GPU_ANY and is_gpu_family(family):
        # "Any" -> the best any single node in the family can offer.
        return max(GPU_MODELS[m]["max_gpus"] for m in _FAMILY_MODEL_ORDER[family])
    return 0


def max_cpus_for(gpu_type, family):
    """Most CPU cores requestable, bounded by the relevant node type."""
    if gpu_type in GPU_MODELS:
        return GPU_MODELS[gpu_type]["max_cpus"]
    if gpu_type == GPU_ANY and is_gpu_family(family):
        return max(GPU_MODELS[m]["max_cpus"] for m in _FAMILY_MODEL_ORDER[family])
    # CPU-only job: bounded by the family's largest node.
    return FAMILIES.get(family, FAMILIES["cpu"])["node_cpus"]


def mem_per_cpu_gb(account, gpu_type, use_free):
    """MaxMemPerCPU (GB) of the partition this request resolves to."""
    return MEM_PER_CPU_GB.get(partition_for(account, gpu_type, use_free), 6)


def required_cpus(mem_gb, gpu_count, account, gpu_type, use_free):
    """The exact CPU count HPC3 will allocate for this request.

    SLURM caps RAM at MaxMemPerCPU GB per core on the target partition, so a job
    needs at least ceil(mem / MaxMemPerCPU) cores -- request fewer and SLURM
    silently bumps --cpus-per-task up to this. GPU jobs are also floored at
    DefCpuPerGPU (2) cores per GPU. Deriving CPUs from memory with this is why
    the picker shows the real number (e.g. 32G on gpu32 -> 6, not 4).
    """
    per = mem_per_cpu_gb(account, gpu_type, use_free)
    need = max(1, math.ceil(mem_gb / per))
    if gpu_type is not None:  # a GPU was requested
        need = max(need, DEF_CPU_PER_GPU * max(1, gpu_count))
    return need


def runtime_cap_hours(use_free):
    """Max wall time (hours) for the chosen tier."""
    return RUNTIME_MAX_HOURS["free" if use_free else "paid"]


def partition_for(account, gpu_type, use_free):
    """The SLURM partition to submit to, from account + GPU type + free flag.

    A specific GPU model pins the family (that's the fix for the RTX6000 ->
    `gpu` mis-routing bug); otherwise the account suffix decides. The free flag
    then selects the free vs paid partition within that family.
    """
    if gpu_type in GPU_MODELS:
        family = GPU_MODELS[gpu_type]["family"]
    else:
        family = account_family(account)
    spec = FAMILIES[family]
    return spec["free"] if use_free else spec["paid"]


def parse_mem_gb(text):
    """Parse a memory string like '64G' / '512M' / '1T' into integer GB."""
    t = str(text).strip().upper()
    if t.endswith("G"):
        return int(float(t[:-1]))
    if t.endswith("T"):
        return int(float(t[:-1]) * 1024)
    if t.endswith("M"):
        return max(1, int(float(t[:-1]) / 1024))
    return int(float(t))


def parse_runtime_hours(text):
    """Parse a SLURM time string into hours.

    Accepts both 'HH:MM:SS' (hours may exceed 24) and 'D-HH:MM:SS'.
    """
    t = str(text).strip()
    days = 0
    if "-" in t:
        d, t = t.split("-", 1)
        days = int(d)
    parts = t.split(":")
    h = int(parts[0]) if parts and parts[0] else 0
    m = int(parts[1]) if len(parts) > 1 and parts[1] else 0
    s = int(parts[2]) if len(parts) > 2 and parts[2] else 0
    return days * 24 + h + m / 60.0 + s / 3600.0
