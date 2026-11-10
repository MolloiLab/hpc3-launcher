#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The "GPUs & nodes" page: who holds each GPU right now, and what is left.

A paid partition and its free mirror are the same machines, so the useful
question about a GPU is not "is it used?" but "by a paid job, a free job, or
nobody?" -- and, for the idle ones, "could a job actually start there?" (often
not: the node's cores are already taken). This page answers both, per GPU model
and per node, from ``modules/cluster_view``.

It is also where a bad node is flagged: the "Avoid" column shows the nodes you
have flagged and the ones other users' queued jobs steer clear of.
"""

import logging
import time

from PyQt5.QtCore import Qt, QTimer, pyqtSlot
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTabWidget, QCheckBox, QTableWidgetItem, QMenu,
)

from modules import cluster_view as cv
from modules import hpc3_constraints as hc
from modules.node_flags import NodeFlagStore
from core.ssh_session import SSHWorker
from ui import theme
from ui.status_view import LiveStatusView
from ui.components import (
    PageHeader, StatTile, SegmentBar, SegmentedControl, Legend, SortItem,
    PillDelegate, BarDelegate, DATA_ROLE, ask_flag_note, button, label, divider,
    make_table, page_layout,
)

logger = logging.getLogger('NodeStatusWidget')

AUTO_REFRESH_MS = 60_000


def _node_state_tone(state):
    base = cv.base_state(state)
    if base == "idle":
        return "ok"
    if base in ("mixed", "allocated", "completing"):
        return "info"
    return "danger"


def _tier_tone(text):
    return "free" if text.lower().startswith("free") else "paid"


def _gb(mb):
    return int(round(mb / 1024.0))


class NodeStatusWidget(QWidget):
    """GPU availability by tier, plus the plain node list."""

    def __init__(self, parent=None, username=None, insights=None, flags=None):
        super().__init__(parent)
        self.username = username
        self.insights = insights
        self.snapshot = None
        self.flags = flags if flags is not None else NodeFlagStore(parent=self)
        self.flags.changed.connect(self._on_flags_changed)
        self._worker = None
        self._last_refresh = 0.0

        self.init_ui()

        self._auto_timer = QTimer(self)
        self._auto_timer.timeout.connect(self._auto_refresh)
        self._auto_timer.start(AUTO_REFRESH_MS)
        self._age_timer = QTimer(self)
        self._age_timer.timeout.connect(self._update_age)
        self._age_timer.start(10_000)

        if self.insights is None:
            self.header.set_subtitle("No SSH key for this user — sign in again to see the cluster.")
            self.refresh_btn.setEnabled(False)

    # --- UI --------------------------------------------------------------------

    def init_ui(self):
        outer = page_layout(self)

        self.header = PageHeader(
            "GPUs & nodes",
            "Who holds each GPU right now: paid jobs, free jobs, or nobody.")
        self.time_label = label("", "muted")
        self.header.add_action(self.time_label)
        self.refresh_btn = button("Refresh", None, self.refresh_data)
        self.header.add_action(self.refresh_btn)
        outer.addWidget(self.header)

        # One tile per GPU model.
        self.tiles_row = QHBoxLayout()
        self.tiles_row.setSpacing(12)
        self.tiles = {}
        for family in ("gpu32", "gpu"):
            for model in hc._FAMILY_MODEL_ORDER[family]:
                vram = hc.GPU_MODELS[model]["vram_gb"]
                tile = StatTile(f"{model.upper()} · {vram} GB", "—", "")
                tile.bar = SegmentBar()
                tile.extra.addWidget(tile.bar)
                tile.split = label("", "faint")
                tile.extra.addWidget(tile.split)
                self.tiles[model] = tile
                self.tiles_row.addWidget(tile, 1)
        outer.addLayout(self.tiles_row)

        outer.addWidget(Legend([("gpu_paid", "Paid job"), ("gpu_free", "Free job"),
                                ("gpu_idle", "Idle")]))

        self.tabs = QTabWidget()
        outer.addWidget(self.tabs, 1)

        # --- GPU nodes tab ---
        gpu_tab = QWidget()
        gpu_layout = QVBoxLayout(gpu_tab)
        gpu_layout.setContentsMargins(0, 12, 0, 0)
        gpu_layout.setSpacing(10)
        filters = QHBoxLayout()
        filters.setSpacing(12)
        self.family_filter = SegmentedControl([("All GPUs", None),
                                               ("L40S / RTX6000", "gpu32"),
                                               ("V100 / A30 / A100", "gpu")])
        self.family_filter.changed.connect(lambda _d: self._render_gpu_nodes())
        filters.addWidget(self.family_filter)
        self.idle_only = QCheckBox("Idle GPUs only")
        self.idle_only.stateChanged.connect(lambda _s: self._render_gpu_nodes())
        filters.addWidget(self.idle_only)
        filters.addStretch(1)
        self.gpu_summary = label("", "muted")
        filters.addWidget(self.gpu_summary)
        self.flag_btn = button("Flag node as bad", None, self._toggle_flag_selected)
        self.flag_btn.setEnabled(False)
        self.flag_btn.setToolTip("Select a node, then flag it to keep your launches off it.")
        filters.addWidget(self.flag_btn)
        gpu_layout.addLayout(filters)

        self.gpu_nodes_table = make_table(
            ["Node", "GPU", "GPUs", "Idle cores", "Free memory",
             "Fits a free job now", "State", "Avoid"], stretch=5)
        self.gpu_nodes_table.setItemDelegateForColumn(2, BarDelegate(self))
        self.gpu_nodes_table.setItemDelegateForColumn(
            6, PillDelegate(tone_for=_node_state_tone, parent=self))
        for col, width in enumerate((140, 76, 240, 84, 100, 0, 80, 130)):
            if width:
                self.gpu_nodes_table.setColumnWidth(col, width)
        self.gpu_nodes_table.itemSelectionChanged.connect(self._update_flag_button)
        self.gpu_nodes_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.gpu_nodes_table.customContextMenuRequested.connect(self._node_menu)
        gpu_layout.addWidget(self.gpu_nodes_table)
        self.tabs.addTab(gpu_tab, "GPU nodes")

        # --- queue tab ---
        queue_tab = QWidget()
        queue_layout = QVBoxLayout(queue_tab)
        queue_layout.setContentsMargins(0, 12, 0, 0)
        queue_layout.setSpacing(10)
        queue_layout.addWidget(label(
            "Jobs waiting for a GPU. \"In line\" jobs are waiting on priority or a free "
            "node; \"held back\" ones are stopped by their own limits (an array throttle, "
            "a per-account GPU cap, a dependency) and are not competing for the next GPU.",
            "muted", wrap=True))
        self.queue_table = make_table(
            ["Partition", "Tier", "Waiting", "In line", "Held back", "Top priority in line"],
            sortable=False)
        self.queue_table.setItemDelegateForColumn(
            1, PillDelegate(tone_for=_tier_tone, parent=self))
        self.queue_table.setMaximumHeight(38 * 5 + 12)
        for col, width in enumerate((150, 90, 100, 100, 110)):
            self.queue_table.setColumnWidth(col, width)
        queue_layout.addWidget(self.queue_table)

        queue_layout.addWidget(label("Your priority in the free queues", "section"))
        queue_layout.addWidget(label(
            "A free job's priority comes almost entirely from the fair-share score of "
            "the account it is submitted under — and free partitions accept any of your "
            "accounts. The Sessions page picks the best one for you.", "muted", wrap=True))
        self.share_table = make_table(
            ["Account", "Fair share", "Free-queue priority", "Rank in free-gpu32",
             "Rank in free-gpu"], sortable=False)
        self.share_table.setItemDelegateForColumn(1, BarDelegate(self))
        for col, width in enumerate((270, 180, 150, 170)):
            self.share_table.setColumnWidth(col, width)
        queue_layout.addWidget(self.share_table, 1)
        self.tabs.addTab(queue_tab, "Queue && priority")

        # --- all nodes tab ---
        all_tab = QWidget()
        all_layout = QVBoxLayout(all_tab)
        all_layout.setContentsMargins(0, 12, 0, 0)
        self.all_nodes_table = make_table(
            ["Node", "State", "Cores in use", "Memory in use", "GPU", "Partitions"])
        self.all_nodes_table.setItemDelegateForColumn(
            1, PillDelegate(tone_for=_node_state_tone, parent=self))
        self.all_nodes_table.setItemDelegateForColumn(2, BarDelegate(self))
        self.all_nodes_table.setItemDelegateForColumn(3, BarDelegate(self))
        for col, width in enumerate((160, 110, 200, 220, 130)):
            self.all_nodes_table.setColumnWidth(col, width)
        all_layout.addWidget(self.all_nodes_table)
        self.tabs.addTab(all_tab, "All nodes")

        outer.addWidget(divider())
        self.status_view = LiveStatusView(self)
        outer.addWidget(self.status_view)

    # --- loading ---------------------------------------------------------------

    def on_shown(self):
        """Called when the page becomes visible: refresh if what we have is old."""
        if time.time() - self._last_refresh > 30:
            self.refresh_data(quiet=self.snapshot is not None)

    def _auto_refresh(self):
        if self.isVisible():
            self.refresh_data(quiet=True)

    @pyqtSlot()
    def refresh_data(self, quiet=False):
        """Re-read the cluster on a worker thread."""
        if self.insights is None:
            return
        if self._worker is not None and self._worker.isRunning():
            return
        self.refresh_btn.setEnabled(False)
        if not quiet:
            self.status_view.start("Reading the cluster", "sinfo and squeue on HPC3")
        self._worker = SSHWorker(lambda rep: self.insights.snapshot(rep, force=True),
                                 parent=self, label="cluster")
        if not quiet:
            self._worker.progress.connect(self.status_view.set_state)
            self._worker.log.connect(self.status_view.append_log)
        self._worker.succeeded.connect(self.update_snapshot)
        self._worker.failed.connect(self.show_error)
        self._worker.start()

    def update_snapshot(self, snapshot):
        """Render a fresh snapshot (runs on the UI thread)."""
        self.refresh_btn.setEnabled(True)
        if not snapshot or not snapshot.get("nodes"):
            self.show_error("HPC3 returned no node data", "Try Refresh in a moment.")
            return
        self.snapshot = snapshot
        self._last_refresh = time.time()
        self._render_tiles()
        self._render_gpu_nodes()
        self._render_queue()
        self._render_all_nodes()
        self._update_age()
        models = snapshot["models"]
        idle = sum(m["idle"] for m in models)
        startable = sum(m["startable"] for m in models)
        self.status_view.finish_ok(
            f"{idle} idle GPUs across the cluster, {startable} on a node that can "
            f"start a free job right now")

    def _update_age(self):
        if not self._last_refresh:
            return
        age = int(time.time() - self._last_refresh)
        self.time_label.setText("Updated just now" if age < 15 else
                                f"Updated {age}s ago" if age < 90 else
                                f"Updated {age // 60} min ago")

    def show_error(self, error_msg, hint=""):
        logger.error("%s %s", error_msg, hint)
        self.refresh_btn.setEnabled(self.insights is not None)
        self.status_view.finish_error(str(error_msg), hint)

    # --- bad nodes -------------------------------------------------------------

    def _selected_node(self):
        items = self.gpu_nodes_table.selectedItems()
        if not items:
            return None
        cell = self.gpu_nodes_table.item(items[0].row(), 0)
        return cell.text() if cell is not None else None

    def _update_flag_button(self):
        node = self._selected_node()
        self.flag_btn.setEnabled(node is not None)
        self.flag_btn.setText("Remove flag" if node and self.flags.is_flagged(node)
                              else "Flag node as bad")

    def _toggle_flag_selected(self):
        node = self._selected_node()
        if node is None:
            return
        if self.flags.is_flagged(node):
            self.flags.unflag(node)
            return
        note = ask_flag_note(self, node)
        if note is not None:
            self.flags.flag(node, note)

    def _node_menu(self, pos):
        row = self.gpu_nodes_table.rowAt(pos.y())
        if row < 0:
            return
        self.gpu_nodes_table.selectRow(row)
        node = self._selected_node()
        if node is None:
            return
        menu = QMenu(self)
        menu.addAction("Remove flag" if self.flags.is_flagged(node)
                       else "Flag node as bad…", self._toggle_flag_selected)
        menu.exec_(self.gpu_nodes_table.viewport().mapToGlobal(pos))

    def _on_flags_changed(self):
        self._render_gpu_nodes()

    def _avoid_cell(self, name, avoided):
        """The "Avoid" column: your own flag first, else what other users do."""
        count = avoided.get(name, 0)
        others = (f"{count} other user{'s' if count != 1 else ''} "
                  f"{'have' if count != 1 else 'has'} a queued job that excludes this node")
        if self.flags.is_flagged(name):
            item = SortItem("You " + self.flags.describe(name), sort_key=1000 + count)
            item.setForeground(theme.color("danger"))
            item.setToolTip(item.text() + (f". Also, {others}." if count else ""))
        elif count >= 2:
            # One person's exclusion list says little (and is often long); two or
            # more agreeing on a node is worth showing.
            item = SortItem(f"{count} users avoid it", sort_key=count)
            suspect = count >= cv.SUSPECT_MIN_USERS
            item.setForeground(theme.color("warn" if suspect else "muted"))
            item.setToolTip(others + (" — it may be faulty." if suspect else "."))
        else:
            item = SortItem("", sort_key=count)
            if count:
                item.setToolTip(others + ".")
        return item

    # --- rendering -------------------------------------------------------------

    def _render_tiles(self):
        rows = {m["model"]: m for m in self.snapshot["models"]}
        for model, tile in self.tiles.items():
            row = rows.get(model)
            if not row:
                tile.set("—", "no nodes reported")
                tile.bar.set_segments([])
                tile.split.setText("")
                continue
            idle, startable = row["idle"], row["startable"]
            if not idle:
                detail = "all in use"
            elif startable == idle:
                detail = "all can start a job now"
            elif startable:
                detail = f"{startable} can start a job now"
            else:
                detail = "none can start a job"
            tile.set(f"{idle} idle", detail, tone="ok" if startable else None)
            if idle > startable:
                tile.detail.setToolTip(
                    f"{idle - startable} idle GPU(s) sit on nodes whose cores or memory "
                    f"are already taken, so no job can use them yet.")
            tile.bar.set_segments([(row["paid"], "gpu_paid"), (row["free"], "gpu_free"),
                                   (idle, "gpu_idle")])
            tile.split.setText(f"{row['paid']} paid · {row['free']} free · "
                               f"{row['total']} total")

    def _render_gpu_nodes(self):
        if not self.snapshot:
            return
        table = self.gpu_nodes_table
        family = self.family_filter.currentData()
        only_idle = self.idle_only.isChecked()
        nodes = [n for n in self.snapshot["nodes"].values() if n["gpu_total"]]
        if family:
            nodes = [n for n in nodes if cv.node_family(n) == family]
        if only_idle:
            nodes = [n for n in nodes if n["gpu_total"] - n["gpu_used"] > 0]
        nodes.sort(key=lambda n: n["name"])
        avoided = self.snapshot.get("avoided", {})
        selected = self._selected_node()    # refilling the table drops the selection

        table.setSortingEnabled(False)
        table.setRowCount(len(nodes))
        for row, node in enumerate(nodes):
            idle = node["gpu_total"] - node["gpu_used"]
            table.setItem(row, 0, SortItem(node["name"]))
            table.setItem(row, 1, SortItem(node["gpu_model"]))

            text = f"{node['gpu_paid']} paid · {node['gpu_free']} free · {idle} idle"
            gpus = SortItem(text, sort_key=idle)
            if node.get("gpu_mine"):
                gpus.setToolTip(f"{node['gpu_mine']} of these are yours")
            gpus.setData(DATA_ROLE, [(node["gpu_paid"], "gpu_paid"),
                                     (node["gpu_free"], "gpu_free"), (idle, "gpu_idle")])
            table.setItem(row, 2, gpus)

            table.setItem(row, 3, SortItem(f"{node['cpus_idle']} of {node['cpus_total']}",
                                           sort_key=node["cpus_idle"]))
            left = max(0, node["mem_mb"] - node["alloc_mem_mb"])
            table.setItem(row, 4, SortItem(f"{_gb(left)} GB", sort_key=left))

            have = cv.room(node, use_free=True)
            if have["gpus"]:
                text = (f"{have['gpus']} GPU{'s' if have['gpus'] != 1 else ''} · "
                        f"{have['cpus']} cores · {have['mem_gb']} GB")
            elif not idle:
                text = "—"
            elif not cv.takes_free_jobs(node):
                text = "Paid jobs only"
            elif cv.base_state(node["state"]) not in ("idle", "mixed"):
                text = "Node isn't accepting jobs"
            else:
                text = "No free cores or memory"
            room_item = SortItem(text, sort_key=have["gpus"] * 1000 + have["cpus"])
            room_item.setToolTip(text)
            if have["gpus"]:
                room_item.setForeground(theme.color("ok"))
            elif idle:
                room_item.setForeground(theme.color("muted"))
            table.setItem(row, 5, room_item)
            table.setItem(row, 6, SortItem(cv.base_state(node["state"]) or node["state"]))
            table.setItem(row, 7, self._avoid_cell(node["name"], avoided))
        table.setSortingEnabled(True)
        if selected:
            for row in range(table.rowCount()):
                if table.item(row, 0).text() == selected:
                    table.selectRow(row)
                    break
        self._update_flag_button()

        idle_total = sum(n["gpu_total"] - n["gpu_used"] for n in nodes)
        startable = sum(cv.room(n)["gpus"] for n in nodes)
        self.gpu_summary.setText(
            f"{len(nodes)} nodes · {idle_total} idle · {startable} startable")

    def _render_queue(self):
        snapshot = self.snapshot
        rows = snapshot.get("queue", [])
        table = self.queue_table
        table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            part = row["partition"]
            table.setItem(r, 0, QTableWidgetItem(part))
            table.setItem(r, 1, QTableWidgetItem("Free" if part.startswith("free") else "Paid"))
            table.setItem(r, 2, QTableWidgetItem(f"{row['pending']:,}"))
            table.setItem(r, 3, QTableWidgetItem(f"{row['competing']:,}"))
            table.setItem(r, 4, QTableWidgetItem(f"{row['blocked']:,}"))
            table.setItem(r, 5, QTableWidgetItem(
                f"{row['top_priority']:,}" if row["competing"] else "—"))

        shares = snapshot.get("fairshare", {})
        pending = snapshot.get("pending", [])
        best = cv.best_free_account(list(shares), shares)
        accounts = sorted(shares, key=lambda a: -shares[a])
        table = self.share_table
        table.setRowCount(len(accounts))
        for r, account in enumerate(accounts):
            priority = cv.free_priority(shares[account])
            name = QTableWidgetItem(account + ("  ·  best for free"
                                               if account == best and len(accounts) > 1 else ""))
            if account == best and len(accounts) > 1:
                font = name.font()
                font.setBold(True)
                name.setFont(font)
            table.setItem(r, 0, name)
            share = QTableWidgetItem(f"{shares[account]:.2f}")
            share.setData(DATA_ROLE, [(shares[account], "accent"),
                                      (1.0 - shares[account], "track")])
            table.setItem(r, 1, share)
            table.setItem(r, 2, QTableWidgetItem(f"≈ {priority:,}"))
            for col, part in ((3, "free-gpu32"), (4, "free-gpu")):
                ahead, competing = cv.jobs_ahead(pending, part, priority)
                text = ("queue is empty" if not competing else
                        f"ahead of all {competing}" if ahead == 0 else
                        f"{ahead} of {competing} ahead of you")
                item = QTableWidgetItem(text)
                if competing and ahead == 0:
                    item.setForeground(theme.color("ok"))
                table.setItem(r, col, item)

    def _render_all_nodes(self):
        table = self.all_nodes_table
        nodes = sorted(self.snapshot["nodes"].values(), key=lambda n: n["name"])
        table.setSortingEnabled(False)
        table.setRowCount(len(nodes))
        for row, node in enumerate(nodes):
            table.setItem(row, 0, SortItem(node["name"]))
            table.setItem(row, 1, SortItem(cv.base_state(node["state"]) or node["state"]))
            total = node["cpus_total"] or 1
            frac = node["cpus_alloc"] / total
            cpu = SortItem(f"{node['cpus_alloc']} / {node['cpus_total']}", sort_key=frac)
            cpu.setData(DATA_ROLE, frac)
            table.setItem(row, 2, cpu)
            mfrac = node["alloc_mem_mb"] / (node["mem_mb"] or 1)
            mem = SortItem(f"{_gb(node['alloc_mem_mb'])} / {_gb(node['mem_mb'])} GB",
                           sort_key=mfrac)
            mem.setData(DATA_ROLE, mfrac)
            table.setItem(row, 3, mem)
            gpu = (f"{node['gpu_used']} / {node['gpu_total']} {node['gpu_model']}"
                   if node["gpu_total"] else "—")
            table.setItem(row, 4, SortItem(gpu, sort_key=node["gpu_total"]))
            table.setItem(row, 5, SortItem(", ".join(node["partitions"])))
        table.setSortingEnabled(True)

    def closeEvent(self, event):
        self._auto_timer.stop()
        self._age_timer.stop()
        super().closeEvent(event)
