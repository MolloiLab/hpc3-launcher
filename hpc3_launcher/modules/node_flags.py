#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Nodes you have flagged as bad, remembered between runs.

A GPU node can be broken in a way Slurm does not notice -- a dead card, a bad
driver -- so it keeps handing the node out and every job that lands there fails.
Flagging a node here keeps every future launch off it (``sbatch --exclude``)
until the flag is removed; an optional note records why.

The flags live in the app's own settings on this computer. Nothing here talks to
the cluster, and flagging a node changes nothing for anyone else.

A flagged name ends up interpolated into a shell command on the login node, so a
name that is not a plausible host name is refused here and again where the
command is built (``vscode_helper.clean_exclude_list``).
"""

import json
import re
import time

from PyQt5.QtCore import QObject, QSettings, pyqtSignal

SETTINGS_KEY = "nodes/flagged"
MAX_NOTE = 120

_NODE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def valid_node(name):
    return isinstance(name, str) and bool(_NODE_NAME_RE.match(name))


def clean_note(note):
    """One short line of plain text."""
    return " ".join(str(note or "").split())[:MAX_NOTE]


def loads(raw):
    """Read stored flags into {node: {"note": str, "at": "YYYY-MM-DD"}}.

    Tolerant by design: settings files get hand-edited and half-written, and a
    bad entry must cost that entry only -- never the launch form.
    """
    try:
        data = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    flags = {}
    for node, info in data.items():
        if not valid_node(node):
            continue
        info = info if isinstance(info, dict) else {}
        at = info.get("at")
        flags[node] = {"note": clean_note(info.get("note")),
                       "at": at if isinstance(at, str) else ""}
    return flags


def dumps(flags):
    return json.dumps(flags, sort_keys=True)


def describe(info):
    """'flagged Nov 9 — GPU 2 dead' for a stored flag."""
    text = "flagged"
    try:
        when = time.strptime(info.get("at") or "", "%Y-%m-%d")
        text += f" {time.strftime('%b', when)} {when.tm_mday}"
    except ValueError:
        pass
    if info.get("note"):
        text += f" — {info['note']}"
    return text


class NodeFlagStore(QObject):
    """The flagged nodes, shared by every page; ``changed`` fires on any edit."""

    changed = pyqtSignal()

    def __init__(self, settings_factory=None, parent=None):
        super().__init__(parent)
        self._settings = settings_factory or (
            lambda: QSettings("MolloiLab", "HPC3 Launcher"))
        self._flags = loads(self._settings().value(SETTINGS_KEY, ""))

    # -- reading --
    def all(self):
        return {node: dict(info) for node, info in self._flags.items()}

    def names(self):
        return sorted(self._flags)

    def is_flagged(self, node):
        return node in self._flags

    def describe(self, node):
        return describe(self._flags[node]) if node in self._flags else ""

    # -- editing --
    def flag(self, node, note=""):
        """Flag ``node`` (or replace its note). Raises ValueError on a bad name."""
        if not valid_node(node):
            raise ValueError(f"Not a valid node name: {node!r}")
        self._flags[node] = {"note": clean_note(note), "at": time.strftime("%Y-%m-%d")}
        self._save()

    def unflag(self, node):
        if self._flags.pop(node, None) is not None:
            self._save()

    def set_names(self, names):
        """Make the flagged set exactly ``names``, keeping the notes of those that stay."""
        names = [n for n in names if valid_node(n)]
        if set(names) == set(self._flags):
            return
        today = time.strftime("%Y-%m-%d")
        self._flags = {n: self._flags.get(n, {"note": "", "at": today}) for n in names}
        self._save()

    def _save(self):
        settings = self._settings()
        settings.setValue(SETTINGS_KEY, dumps(self._flags))
        settings.sync()
        self.changed.emit()
