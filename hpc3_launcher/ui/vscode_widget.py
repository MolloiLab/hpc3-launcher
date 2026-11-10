#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The Sessions page: launch VSCode sessions and connect to them.

The form is a cascade, top to bottom -- tier, account, GPU, GPU count, memory,
CPUs, run time -- and each choice narrows the ones below it, so the form can only
ever describe a job HPC3 will accept (see ``modules/hpc3_constraints``).

Under the form sits what makes a *free* job start sooner, computed from the live
cluster snapshot (``modules/cluster_view``):

  * which of your accounts gives a free job the best queue priority (and, by
    default, that account is chosen for you),
  * whether the request fits on a GPU that is idle right now, and if not, the
    largest request that would,
  * Slurm's own start-time estimate for exactly this request, on demand, and the
    same estimate for every other GPU model side by side.

Nodes you have flagged as bad (``modules/node_flags``) are remembered and every
launch is kept off them; a node that several other users' queued jobs avoid is
pointed out so it can be flagged in one click.
"""

import logging
import time
from datetime import datetime

from PyQt5.QtCore import Qt, pyqtSlot, QTimer, QSettings
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QComboBox, QSpinBox, QTextEdit,
    QTabWidget, QScrollArea, QFrame, QTableWidgetItem, QApplication, QSizePolicy,
)

from modules.vscode_helper import VSCodeManager, build_sbatch_command, is_terminal_state
from modules.auth import HPC_SERVER, get_all_existing_users
from modules.balance import BalanceManager
from modules import hpc3_constraints as hc
from modules import cluster_view as cv
from modules.node_flags import NodeFlagStore, valid_node
from core.ssh_session import SSHWorker
from ui import theme
from ui.status_view import LiveStatusView
from ui.checkable_combo import CheckableComboBox
from ui.components import (
    Card, PageHeader, SegmentedControl, PillDelegate, ask_flag_note, button, dot, label,
    divider, make_table, page_layout, set_dot,
)

logger = logging.getLogger('VSCodeWidget')

MEMORY_OPTIONS = ["4G", "8G", "16G", "24G", "32G", "48G", "64G", "96G", "128G", "192G"]
TIME_OPTIONS = ["1:00:00", "2:00:00", "4:00:00", "8:00:00", "12:00:00",
                "1-00:00:00", "2-00:00:00", "3-00:00:00", "7-00:00:00", "14-00:00:00"]


def encode_choice(family, gpu_type):
    """A GPU-dropdown row as one string ("gpu32|RTX6000", "gpu32|", "cpu|none").

    Combo data is kept as a plain string because Qt compares strings reliably;
    a (family, None) tuple does not survive ``findData``.
    """
    return f"{family}|{'none' if gpu_type is None else gpu_type}"


def decode_choice(data):
    """Inverse of ``encode_choice``; None for a placeholder row."""
    if not data or "|" not in data:
        return None
    family, gpu_type = data.split("|", 1)
    return family, (None if gpu_type == "none" else gpu_type)


def _tier_tone(partition):
    return "free" if (partition or "").startswith("free") else "paid"


def friendly_time(iso):
    """'2026-11-10T12:07:47' -> 'Tue 12:07' (or 'today 12:07')."""
    try:
        when = datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")
    except (ValueError, TypeError):
        return iso or ""
    days = (when.date() - datetime.now().date()).days
    if days == 0:
        return f"today {when:%H:%M}"
    if days == 1:
        return f"tomorrow {when:%H:%M}"
    if 1 < days < 7:
        return f"{when:%a %H:%M}"
    return f"{when:%b %d, %H:%M}"


def friendly_wait(iso, now=None):
    """How long until an ISO time, in a few characters: 'now', 'in 20 min',
    'in 11 h', 'in 3.7 days'."""
    try:
        when = datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S")
    except (ValueError, TypeError):
        return iso or ""
    minutes = (when - (now or datetime.now())).total_seconds() / 60.0
    if minutes < 2:
        return "now"
    if minutes < 90:
        return f"in {int(round(minutes))} min"
    if minutes < 36 * 60:
        return f"in {int(round(minutes / 60.0))} h"
    return f"in {minutes / 1440.0:.1f} days"


class VSCodeWidget(QWidget):
    """VSCode remote sessions: configure, launch, and connect."""

    SYNC_INTERVAL_MS = 60_000  # how often the queue is checked against ~/.ssh/config

    def __init__(self, parent=None, username=None, insights=None, flags=None):
        super().__init__(parent)

        # User information
        self.username = username
        self.vscode_manager = None
        self.balance_manager = None
        self.insights = insights      # shared read-only cluster snapshot source
        self.snapshot = None
        # Nodes flagged as bad; every launch is kept off them. Shared with the
        # GPUs & nodes page when the main window passes one in.
        self.flags = flags if flags is not None else NodeFlagStore(self._settings, self)
        self.flags.changed.connect(self._on_flags_changed)

        # Account information
        self.accounts = []

        # Running VSCode sessions, keyed by job id -> info dict (may carry 'config').
        # Multiple sessions can run at once; the table below lists them all.
        self.sessions = {}
        self.selected_job_id = None

        # {partition: [(node, state)]} from sinfo, for the "Exclude Nodes" picker.
        self.partition_nodes = {}
        self._exclude_items = None

        # Pending submit config; guard to suppress reentrant combo signals.
        self._pending_submit = None
        self._applying = False
        # True once the user picks an account by hand; until then the free tier
        # keeps the best-priority account selected for them.
        self._account_pinned = False
        self._sync_worker = None
        self._snapshot_worker = None
        self._shrink_target = None
        self._compare_worker = None
        self._compare_key = None
        self._compare_rows = []

        # Initialization complete flag
        self.initialization_complete = False

        # Initialize UI
        self.init_ui()

        # Use timer to delay initialization of other managers to avoid blocking UI at startup
        QTimer.singleShot(500, self.delayed_init_managers)

    # --- managers + data loading ----------------------------------------------

    def delayed_init_managers(self):
        """Build managers just after first paint (keeps startup snappy)."""
        try:
            self.init_managers()
            self.initialization_complete = True
        except Exception as e:
            logger.error(f"Failed to initialize managers: {e}")
            self.status_view.finish_error("initialization failed", str(e))

    def init_managers(self):
        """Initialize VSCode manager and balance manager"""
        if not self.username:
            self.status_label.setText("No username — sign in again.")
            return

        # Get SSH key path
        users = get_all_existing_users()
        key_path = None

        for user in users:
            if user['username'] == self.username:
                key_path = user['key_path']
                break

        if not key_path:
            self.status_label.setText(f"No SSH key for {self.username} — sign in again to create one.")
            return

        try:
            # Create manager objects in the main thread
            self.vscode_manager = VSCodeManager(
                hostname=HPC_SERVER,
                username=self.username,
                key_path=key_path
            )

            # Connect signals -- all route into the multi-session table.
            self.vscode_manager.vscode_job_submitted.connect(self._upsert_session)
            self.vscode_manager.vscode_job_status_updated.connect(self._upsert_session)
            self.vscode_manager.vscode_config_ready.connect(self._upsert_session)
            self.vscode_manager.error_occurred.connect(self.show_error)

            # Connect SSH config signals
            self.vscode_manager.ssh_config_added.connect(self.on_ssh_config_added)
            self.vscode_manager.ssh_config_removed.connect(self.on_ssh_config_removed)

            # Create balance manager to get account information
            self.balance_manager = BalanceManager(
                hostname=HPC_SERVER,
                username=self.username,
                key_path=key_path
            )

            # Load accounts + GPU types on a worker thread, with the same live
            # spinner as the other pages. The old version used a raw threading.Thread
            # that populated combo boxes directly off the worker thread -- touching
            # Qt widgets from a non-GUI thread, which aborts the whole app.
            self._load_initial_data()
        except Exception as e:
            self.status_view.finish_error("could not initialize managers", str(e))

    def _load_initial_data(self):
        """Fetch accounts + cluster snapshot + any running session on a worker thread."""
        if not self.vscode_manager:
            return
        self.status_label.setText("Loading your accounts…")
        self.status_view.start("Loading options", "reading your accounts and the GPU nodes")
        self._init_worker = SSHWorker(self._fetch_options, parent=self, label="vscode-init")
        self._init_worker.progress.connect(self.status_view.set_state)
        self._init_worker.log.connect(self.status_view.append_log)
        self._init_worker.failed.connect(self._on_init_failed)
        self._init_worker.succeeded.connect(self._on_options_loaded)
        self._init_worker.start()

    def _fetch_options(self, rep):
        # GPU types are no longer discovered over SSH: they're derived locally from
        # the tier and account (see _rebuild_gpu_options). The old sinfo probe
        # truncated long gres tokens (e.g. "gpu:rtx6000" -> "rtx600") and couldn't
        # model the account->partition rules anyway.
        rep.state("loading", "reading your accounts")
        balance = self.balance_manager.get_user_balance(reporter=rep)
        accounts = []
        if balance and balance.get('accounts'):
            # sbank prints account names in capitals; Slurm itself (sshare, squeue,
            # sbatch) uses lower case. One spelling from here on, or the priority
            # lookup below matches nothing.
            accounts = [{'name': a['name'].lower(), 'is_personal': a['is_personal'],
                         'available': a['available']} for a in balance['accounts']]
        rep.state("checking", "looking for a running VSCode session")
        try:
            # Also brings ~/.ssh/config up to date with the queue.
            running = self.vscode_manager.sync_ssh_config()
        except Exception as e:
            logger.error(f"Could not sync SSH config with the queue: {e}")
            running = []
        rep.state("loading", "listing cluster nodes")
        nodes = self.vscode_manager.get_partition_nodes()  # {} on failure; picker degrades
        snapshot = None
        if self.insights is not None:
            try:
                snapshot = self.insights.snapshot(rep)
            except Exception as e:  # the form still works without the live picture
                logger.warning(f"Could not read the cluster snapshot: {e}")
        return {'accounts': accounts, 'running': running, 'nodes': nodes,
                'snapshot': snapshot}

    def _on_init_failed(self, message, hint=""):
        self.status_view.finish_error(message, hint)
        self.status_label.setText("Could not load your accounts")

    def _on_options_loaded(self, data):
        self.accounts = [dict(a, name=a['name'].lower()) for a in data['accounts']]
        self.partition_nodes = data.get('nodes') or {}
        self.snapshot = data.get('snapshot')
        self._populate_account_combo()
        self._rebuild_gpu_options()
        self._restore_last_request()
        self._apply_cascade()
        self.status_view.finish_ok("ready")

        # Seed the sessions table with every VSCode job already queued.
        self._apply_queue(data['running'] or [])
        # Auto-select the first session so its details show.
        if self.sessions and self.selected_job_id is None:
            self.sessions_table.selectRow(0)

        # From now on, keep the table and ~/.ssh/config in step with the queue: a
        # session may start hours after it was submitted, or end from anywhere.
        self._sync_timer = QTimer(self)
        self._sync_timer.timeout.connect(self._sync_with_queue)
        self._sync_timer.start(self.SYNC_INTERVAL_MS)

    def _sync_with_queue(self):
        if not self.vscode_manager:
            return
        if self._sync_worker is None or not self._sync_worker.isRunning():
            self._sync_worker = SSHWorker(lambda rep: self.vscode_manager.sync_ssh_config(),
                                          parent=self, label="vscode-sync")
            self._sync_worker.succeeded.connect(self._apply_queue)
            self._sync_worker.failed.connect(
                lambda message, hint: logger.warning(f"SSH config sync skipped: {message}"))
            self._sync_worker.start()
        self.refresh_snapshot()

    def refresh_snapshot(self, force=False):
        """Re-read the cluster picture in the background (idle GPUs, queue, priority)."""
        if self.insights is None:
            return
        if self._snapshot_worker is not None and self._snapshot_worker.isRunning():
            return
        self._snapshot_worker = SSHWorker(
            lambda rep: self.insights.snapshot(force=force), parent=self, label="snapshot")
        self._snapshot_worker.succeeded.connect(self._on_snapshot)
        self._snapshot_worker.failed.connect(
            lambda message, hint: logger.warning(f"Cluster snapshot skipped: {message}"))
        self._snapshot_worker.start()

    def _on_snapshot(self, snapshot):
        first = self.snapshot is None
        self.snapshot = snapshot
        if first:
            self._populate_account_combo()   # priorities are now known
        self._apply_cascade()

    def _apply_queue(self, jobs):
        """Make the sessions table list exactly the queued VSCode jobs."""
        live = {job['job_id']: job for job in jobs if job.get('job_id')}
        for jid in list(self.sessions):
            if jid not in live:
                self._upsert_session({'job_id': jid, 'status': 'COMPLETED'})
        for jid, job in live.items():
            info = {'job_id': jid, 'status': job.get('status'),
                    'node': job.get('node'), 'hostname': job.get('hostname') or job.get('node')}
            for key in ('partition', 'priority', 'reason', 'start_estimate', 'account',
                        'tres', 'time_limit'):
                info[key] = job.get(key)
            # A job that has started is no longer waiting on anything.
            if job.get('status') == 'RUNNING' and jid in self.sessions:
                self.sessions[jid].pop('reason', None)
                self.sessions[jid].pop('start_estimate', None)
            if job.get('config'):
                info.update(config=job['config'], port=job.get('port'))
            self._upsert_session(info)

    # --- accounts --------------------------------------------------------------

    def _fairshare(self):
        return (self.snapshot or {}).get('fairshare') or {}

    def _best_free_account(self):
        return cv.best_free_account([a['name'] for a in self.accounts], self._fairshare())

    def _populate_account_combo(self):
        """List the accounts, labelled for the current tier.

        Free: any account may be used and none is charged, so what distinguishes
        them is the queue priority each one earns -- that is what the label shows,
        best first. Paid: the label shows the SUs left to spend.
        """
        use_free = bool(self.free_option_check.currentData())
        current = self.account_combo.currentData()
        shares = self._fairshare()
        best = self._best_free_account()
        accounts = list(self.accounts)
        if use_free and shares:
            accounts.sort(key=lambda a: -shares.get(a['name'], -1.0))

        self.account_combo.blockSignals(True)
        self.account_combo.clear()
        if not accounts:
            self.account_combo.addItem("Loading…" if not self.initialization_complete
                                       else "No accounts found", None)
        for account in accounts:
            name = account['name']
            if use_free and name in shares:
                text = f"{name}  ·  priority {cv.free_priority(shares[name]):,}"
                if name == best and len(shares) > 1:
                    text += "  ·  best for free"
            elif use_free:
                text = name
            else:
                text = f"{name}  ·  {account['available']:,} SUs"
            self.account_combo.addItem(text, name)

        target = current if (self._account_pinned or not use_free) else (best or current)
        index = self.account_combo.findData(target) if target else -1
        if index < 0 and self.account_combo.count():
            index = 0
            if not use_free:   # paid: open on the account whose family has GPUs
                for i, account in enumerate(accounts):
                    if hc.is_gpu_family(hc.account_family(account['name'])):
                        index = i
                        break
        self.account_combo.setCurrentIndex(index)
        self.account_combo.blockSignals(False)

    # --- multi-session model ---------------------------------------------------

    @pyqtSlot(dict)
    def _upsert_session(self, info):
        """Merge a job-info/status/config update into the sessions table.

        Fed by all three manager signals (submitted / status / config). Terminal
        states drop the session; everything else merges in. If the touched job is
        the selected one, its detail panes refresh too.
        """
        if not info:
            return
        jid = info.get('job_id')
        if not jid:
            return
        status = info.get('status')
        # is_terminal_state, not `in (...)`: sacct reports "CANCELLED by 3012547",
        # which never matched, so dead sessions stayed in the table -- and selecting
        # one wrote its (nonexistent) node into ~/.ssh/config.
        if is_terminal_state(status):
            self.sessions.pop(jid, None)
            if self.selected_job_id == jid:
                self.selected_job_id = None
                self.job_info_text.clear()
                self.config_text.clear()
                self.host_label.setText("")
                self.copy_host_btn.setEnabled(False)
        else:
            cur = self.sessions.get(jid, {})
            cur.update({k: v for k, v in info.items() if v is not None})
            self.sessions[jid] = cur
            # First session in: select it so the user sees something.
            if self.selected_job_id is None:
                self.selected_job_id = jid
        self._render_sessions_table()
        if self.selected_job_id == jid and jid in self.sessions:
            sess = self.sessions[jid]
            self._render_job_info(sess)
            if sess.get('config'):
                self._render_config(sess)

    def _waiting_text(self, info):
        node = info.get('node')
        status = (info.get('status') or '').upper()
        if info.get('config') or status == 'RUNNING':
            return node or "Running"
        if status != 'PENDING':
            return f"{node} · starting…" if node else "Starting…"
        bits = []
        if info.get('reason'):
            bits.append({"Priority": "Waiting on priority",
                         "Resources": "Next in line for a node"}.get(info['reason'],
                                                                     info['reason']))
        if info.get('start_estimate'):
            bits.append(f"est. {friendly_time(info['start_estimate'])}")
        if info.get('priority') is not None:
            bits.append(f"priority {info['priority']:,}")
        return " · ".join(bits) or "Queued"

    def _render_sessions_table(self):
        table = self.sessions_table
        table.blockSignals(True)
        table.setRowCount(0)
        for row, (jid, info) in enumerate(sorted(self.sessions.items())):
            table.insertRow(row)
            id_item = QTableWidgetItem(str(jid))
            id_item.setData(Qt.UserRole, jid)
            table.setItem(row, 0, id_item)
            table.setItem(row, 1, QTableWidgetItem(info.get('status') or '—'))
            partition = info.get('partition') or ''
            table.setItem(row, 2, QTableWidgetItem(partition or '—'))
            status_item = QTableWidgetItem(self._waiting_text(info))
            status_item.setToolTip(status_item.text())
            table.setItem(row, 3, status_item)
            if jid == self.selected_job_id:
                table.selectRow(row)
        table.blockSignals(False)
        self.cancel_btn.setEnabled(self.selected_job_id in self.sessions)
        self._update_flag_button()
        self._rebuild_exclude_options()  # a session's node may have just appeared
        n = len(self.sessions)
        running = sum(1 for s in self.sessions.values()
                      if (s.get('status') or '').upper() == 'RUNNING')
        self.sessions_label.setText(
            "No sessions yet — launch one on the left." if not n else
            f"{running} running · {n - running} waiting")

    def _on_session_selected(self):
        items = self.sessions_table.selectedItems()
        if not items:
            self.selected_job_id = None
            self.cancel_btn.setEnabled(False)
            self._update_flag_button()
            return
        jid = self.sessions_table.item(items[0].row(), 0).data(Qt.UserRole)
        self.selected_job_id = jid
        self.cancel_btn.setEnabled(True)
        self._update_flag_button()
        info = self.sessions.get(jid)
        if not info:
            return
        self._render_job_info(info)
        if info.get('config'):
            self._render_config(info)
        elif (info.get('status') or '').upper() == 'RUNNING':
            self.host_label.setText("Fetching connection details…")
            self.copy_host_btn.setEnabled(False)
            self.config_text.setPlainText("Fetching connection details…")
            self._fetch_config_for(jid)
        else:
            self.host_label.setText("Not started yet — the host appears when a node is assigned.")
            self.copy_host_btn.setEnabled(False)
            self.config_text.setPlainText("")

    def _fetch_config_for(self, jid):
        """Parse one session's connection config on a worker thread, lazily, and
        ensure its ~/.ssh/config block exists (heals a missing/clobbered block)."""
        worker = SSHWorker(lambda rep: self.vscode_manager.ensure_ssh_config(jid),
                           parent=self, label="vscode-config")
        worker.succeeded.connect(
            lambda cfg: self._upsert_session({'job_id': jid, 'config': cfg,
                                              'hostname': cfg.get('hostname'),
                                              'port': cfg.get('port')}) if cfg else None)
        worker.start()

    def _render_job_info(self, info):
        """Render one session's details into the Job Information pane."""
        lines = [f"Job ID:      {info.get('job_id', 'N/A')}",
                 f"Status:      {info.get('status', 'N/A')}"]
        if info.get('partition'):
            tier = "free" if info['partition'].startswith('free') else "paid"
            lines.append(f"Partition:   {info['partition']}  ({tier})")
        if info.get('node'):
            flagged = self.flags.describe(info['node'])
            lines.append(f"Node:        {info['node']}" + (f"  ({flagged})" if flagged else ""))
        if info.get('account'):
            lines.append(f"Account:     {info['account']}")
        if info.get('cpus'):
            lines.append(f"CPUs:        {info['cpus']}")
        if info.get('memory'):
            lines.append(f"Memory:      {info['memory']}")
        if info.get('gpu_type'):
            lines.append(f"GPU:         {info.get('gpu_count', 1)} × {info['gpu_type']}")
        elif info.get('tres'):
            lines.append(f"GPU:         {info['tres']}")
        if info.get('time_limit'):
            lines.append(f"Time limit:  {info['time_limit']}")
        if (info.get('status') or '').upper() == 'PENDING':
            if info.get('priority') is not None:
                lines.append(f"Priority:    {info['priority']:,}")
            if info.get('reason'):
                lines.append(f"Waiting on:  {info['reason']}")
            if info.get('start_estimate'):
                lines.append(f"Est. start:  {friendly_time(info['start_estimate'])}")
        if info.get('exclude_nodes'):
            lines.append(f"Kept off:    {', '.join(info['exclude_nodes'])}")
        if 'submit_time' in info:
            lines.append("Submitted:   " +
                         time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(info['submit_time'])))
        if info.get('config'):
            lines.append("SSH config:  written to ~/.ssh/config")
        self.job_info_text.setPlainText("\n".join(lines))

    def _render_config(self, info):
        """Render one session's VSCode connection instructions."""
        config = info.get('config') or {}
        host = config.get('hostname', '?')
        self.host_label.setText(host)
        self.copy_host_btn.setEnabled(host != '?')
        text = (
            "In VSCode press F1, run \"Remote-SSH: Connect to Host…\" and pick the host "
            "above. It is already in your ~/.ssh/config.\n\n"
            f"User {config.get('user', self.username)} · port {config.get('port', '?')} · "
            f"job {info.get('job_id', 'N/A')}\n\n"
            "When you're done, select the session and press Cancel session."
        )
        self.config_text.setPlainText(text)

    def _copy_host(self):
        host = self.host_label.text().strip()
        if host:
            QApplication.clipboard().setText(host)
            self.status_label.setText(f"Copied {host}")

    # --- UI --------------------------------------------------------------------

    def _field(self, grid, row, text, widget, col=0, span=1):
        """One labelled field in the form grid (label above the control)."""
        box = QVBoxLayout()
        box.setSpacing(5)
        box.addWidget(label(text, "field"))
        box.addWidget(widget)
        grid.addLayout(box, row, col, 1, span)

    def _advice_line(self, layout, action=None, on_click=None):
        """One row of the outlook card. Returns (light, text, action button)."""
        row = QHBoxLayout()
        row.setSpacing(10)
        light = dot()
        holder = QVBoxLayout()              # drops the light onto the first text line
        holder.setContentsMargins(0, 5, 0, 0)
        holder.addWidget(light)
        holder.addStretch(1)
        row.addLayout(holder)
        text = label("", wrap=True)
        row.addWidget(text, 1)
        act = None
        if action:
            act = button(action, "link", on_click)
            act.hide()
            row.addWidget(act, 0, Qt.AlignTop)
        layout.addLayout(row)
        return light, text, act

    def _set_line(self, light, text, html, tone=None):
        """Fill one outlook row, or hide it when there is nothing to say."""
        light.setVisible(bool(html))
        text.setVisible(bool(html))
        if html:
            text.setText(html)
            set_dot(light, tone)

    def _toggle_more(self, checked=None):
        shown = self.more_btn.isChecked() if checked is None else bool(checked)
        self.more_btn.setChecked(shown)
        self.more_box.setVisible(shown)
        self._update_more_label()

    def _update_more_label(self):
        avoided = len(self._avoided()) if self.snapshot or self.flags.names() else 0
        text = "Fewer options" if self.more_btn.isChecked() else "More options"
        if avoided and not self.more_btn.isChecked():
            text += f" · {avoided} node{'s' if avoided != 1 else ''} avoided"
        self.more_btn.setText(text)

    def init_ui(self):
        """Initialize UI components"""
        outer = page_layout(self)

        self.header = PageHeader(
            "VSCode sessions",
            "Launch a compute session on HPC3 and connect to it from VSCode.")
        outer.addWidget(self.header)

        columns = QHBoxLayout()
        columns.setSpacing(16)

        # ---- left: the request form, in dependency order ----------------------
        # Each choice constrains the ones below it (tier -> account -> GPU ->
        # #GPUs -> memory -> #CPUs -> run time). The cascade (_apply_cascade)
        # keeps only valid HPC3 combinations selectable.
        form_scroll = QScrollArea()
        form_scroll.setWidgetResizable(True)
        form_scroll.setFrameShape(QFrame.NoFrame)
        form_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        form_scroll.setMinimumWidth(400)
        form_scroll.setMaximumWidth(440)
        form_host = QWidget()
        form_host_layout = QVBoxLayout(form_host)
        form_host_layout.setContentsMargins(0, 0, 6, 0)

        form = Card("New session")
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        row = 0

        # 1. Tier -- first, because it decides which accounts and GPUs are legal:
        #    free partitions take any account; paid ones check the account suffix.
        self.free_option_check = SegmentedControl([("Free", True), ("Paid", False)])
        self.free_option_check.setToolTips([
            "Costs no SUs. Waits in the free queue, and can be cancelled if a paid job "
            "needs the node.",
            "Spends SUs from the account. Starts ahead of free jobs and is never preempted."])
        self.free_option_check.changed.connect(self.on_free_changed)
        self._field(grid, row, "Tier", self.free_option_check, 0, 2)
        row += 1

        # 2. Account -- on the paid tier its suffix sets the partition family; on
        #    the free tier it sets your queue priority.
        self.account_combo = QComboBox()
        self.account_combo.addItem("Loading…", None)
        self.account_combo.currentIndexChanged.connect(self.on_account_changed)
        self.account_combo.activated.connect(self._on_account_picked)
        self._field(grid, row, "Account", self.account_combo, 0, 2)
        row += 1

        # 3. GPU -- only the models this tier + account can actually reach.
        self.gpu_combo = QComboBox()
        self.gpu_combo.addItem("Select an account first", None)
        self.gpu_combo.setEnabled(False)
        self.gpu_combo.currentIndexChanged.connect(self.on_gpu_changed)
        self._field(grid, row, "GPU", self.gpu_combo, 0, 2)
        row += 1

        # 4. Number of GPUs -- max clamped to the chosen GPU's per-node limit.
        self.gpu_count_spinbox = QSpinBox()
        self.gpu_count_spinbox.setMinimum(1)
        self.gpu_count_spinbox.setMaximum(8)
        self.gpu_count_spinbox.setValue(1)
        self.gpu_count_spinbox.setEnabled(False)  # until a GPU is selected
        self.gpu_count_spinbox.valueChanged.connect(self.on_gpu_count_changed)
        self._field(grid, row, "GPUs", self.gpu_count_spinbox, 0)

        # 5. Memory -- HPC3 caps RAM per core, so more memory means more cores.
        self.memory_combo = QComboBox()
        for mem in MEMORY_OPTIONS:
            self.memory_combo.addItem(mem)
        self.memory_combo.setCurrentText("16G")
        self.memory_combo.currentIndexChanged.connect(self.on_memory_changed)
        self._field(grid, row, "Memory", self.memory_combo, 1)
        row += 1

        # 6. CPUs -- min from memory (GB-per-core cap), max from the node type.
        self.cpu_spinbox = QSpinBox()
        self.cpu_spinbox.setMinimum(1)
        self.cpu_spinbox.setMaximum(128)
        self.cpu_spinbox.setValue(2)
        self.cpu_spinbox.valueChanged.connect(self.on_cpu_changed)
        # (placed under "More options" below: it is derived, rarely touched)

        # 7. Run time -- capped at 3 days on free queues, 14 days on paid.
        self.time_combo = QComboBox()
        for t in TIME_OPTIONS:
            self.time_combo.addItem(t)
        self.time_combo.setCurrentText("8:00:00")
        self.time_combo.currentIndexChanged.connect(self.on_time_changed)
        self._field(grid, row, "Run time", self.time_combo, 0, 2)

        # 8. Bad nodes -- tick any node Slurm should keep your jobs off (sbatch
        #    --exclude), e.g. one whose GPU is broken. A tick is a flag: it is
        #    remembered until you untick it. Flagged nodes are listed first, then
        #    your own sessions' nodes, so "not that node again" is two clicks.
        self.exclude_combo = CheckableComboBox(empty_text="None flagged — any node")
        self.exclude_combo.setToolTip(
            "Tick a node to keep every launch off it. Remembered until you untick it.")
        self.exclude_combo.checked_changed.connect(self.on_exclude_changed)
        form.body.addLayout(grid)

        # The two fields that are rarely touched stay folded away, so the form
        # reads as five choices rather than eight.
        self.more_btn = button("More options", "ghost", self._toggle_more)
        self.more_btn.setCheckable(True)
        form.body.addWidget(self.more_btn, 0, Qt.AlignLeft)
        self.more_box = QWidget()
        more = QGridLayout(self.more_box)
        more.setContentsMargins(0, 0, 0, 0)
        more.setHorizontalSpacing(12)
        more.setVerticalSpacing(10)
        more.setColumnStretch(0, 1)
        more.setColumnStretch(1, 1)
        self._field(more, 0, "CPUs (set by memory)", self.cpu_spinbox, 0)
        self._field(more, 1, "Bad nodes to avoid", self.exclude_combo, 0, 2)
        self.more_box.hide()
        form.body.addWidget(self.more_box)

        # Launch button -- each click starts a NEW session (multiple may run).
        self.submit_btn = button("Launch session", "primary", self.submit_job)
        self.submit_btn.setEnabled(False)  # until an account is selected
        form.body.addWidget(self.submit_btn)

        # What will actually be requested, in one quiet line under the button.
        self.status_label = label("Loading…", "summary", wrap=True)
        form.body.addWidget(self.status_label)

        form_host_layout.addWidget(form)
        form_host_layout.addStretch(1)
        form_scroll.setWidget(form_host)
        columns.addWidget(form_scroll, 0)

        # ---- right: free-tier outlook, sessions, connection details -------------
        right = QVBoxLayout()
        right.setSpacing(16)

        # What decides how soon a free job starts: priority, fit, bad nodes, and
        # Slurm's own estimates. First thing on the right so it is never scrolled
        # out of sight (it used to sit under the form, below the fold).
        panel = Card()                       # the one floating surface on the right
        panel.body.setSpacing(14)
        self.advice_card = Card("Free-tier outlook", kind="flat")
        advice = self.advice_card.body
        advice.setSpacing(6)
        # Slurm's start estimate for this same size on every GPU model.
        self.compare_btn = button("Check start times", None, self.compare_starts)
        self.compare_btn.setToolTip(
            "Asks Slurm when this size of job would start on each GPU model. "
            "Nothing is submitted.")
        self.advice_card.add_action(self.compare_btn)

        # Each line: a status light, plain words, and at most one quiet action.
        self.priority_dot, self.priority_label, self.use_best_btn = self._advice_line(
            advice, "Use it", self._use_best_account)
        self.fit_dot, self.fit_label, self.shrink_btn = self._advice_line(
            advice, "Shrink to fit", self._shrink_to_fit)
        self.suspect_dot, self.suspect_label, self.avoid_suspect_btn = self._advice_line(
            advice, "Avoid it", self._avoid_suspects)
        self._suspects = []
        self.time_dot, self.time_hint_label, _unused = self._advice_line(advice)

        # One small tile per GPU model, soonest first (filled by compare_starts).
        self.compare_box = QWidget()
        self.compare_row = QHBoxLayout(self.compare_box)
        self.compare_row.setContentsMargins(0, 4, 0, 0)
        self.compare_row.setSpacing(8)
        self.compare_box.hide()
        advice.addWidget(self.compare_box)
        advice.addSpacing(4)
        advice.addWidget(divider())          # hidden with the group on the paid tier
        panel.body.addWidget(self.advice_card, 0)

        sessions_card = Card("Your sessions", kind="flat")
        self.sessions_label = label("No sessions yet — launch one on the left.", "muted")
        sessions_card.header.insertWidget(1, self.sessions_label, 0, Qt.AlignVCenter)
        # Cancel the session selected in the table below.
        self.cancel_btn = button("Cancel session", "danger", self.cancel_selected_session)
        self.cancel_btn.setEnabled(False)  # Enabled when a session is selected
        # Flag the selected session's node as bad: later launches stay off it.
        self.flag_btn = button("Flag node as bad", "ghost", self._toggle_flag_selected)
        self.flag_btn.setEnabled(False)
        sessions_card.add_action(self.flag_btn)
        sessions_card.add_action(self.cancel_btn)

        self.sessions_table = make_table(["Job", "State", "Queue", "Status"],
                                         sortable=False)
        self.sessions_table.horizontalHeader().hide()   # four self-evident columns
        self.sessions_table.setItemDelegateForColumn(1, PillDelegate(parent=self))
        self.sessions_table.setItemDelegateForColumn(
            2, PillDelegate(tone_for=_tier_tone, parent=self))
        self.sessions_table.setColumnWidth(0, 104)
        self.sessions_table.setColumnWidth(1, 104)
        self.sessions_table.setColumnWidth(2, 108)
        # "Ignored": inside the scrolling column Qt sizes by preferred height, and a
        # table's built-in preference (192 px) would push the Connect card off screen.
        self.sessions_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        self.sessions_table.setMinimumHeight(110)
        self.sessions_table.setProperty("clickableRows", True)
        self.sessions_table.itemSelectionChanged.connect(self._on_session_selected)
        sessions_card.body.addWidget(self.sessions_table)
        panel.body.addWidget(sessions_card, 3)

        details = Card("Connect", kind="flat")
        host_row = QHBoxLayout()
        host_row.setSpacing(8)
        self.host_label = label("", "mono")
        self.host_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        host_row.addWidget(self.host_label, 1)
        self.copy_host_btn = button("Copy host", "ghost", self._copy_host)
        self.copy_host_btn.setEnabled(False)
        host_row.addWidget(self.copy_host_btn)
        details.header.addLayout(host_row, 2)

        result_widget = QTabWidget()
        self.config_text = QTextEdit()
        self.config_text.setReadOnly(True)
        self.config_text.setProperty("flat", True)
        self.config_text.setPlaceholderText(
            "Select a running session to see how to connect to it from VSCode.")
        self.job_info_text = QTextEdit()
        self.job_info_text.setReadOnly(True)
        self.job_info_text.setPlaceholderText("Select a session to see its details.")
        self.job_info_text.setProperty("flat", True)
        self.job_info_text.setFont(theme.mono_font(12))
        result_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        result_widget.setMinimumHeight(132)
        result_widget.addTab(self.config_text, "How to connect")
        result_widget.addTab(self.job_info_text, "Job details")
        details.body.addWidget(result_widget)
        panel.body.addWidget(details, 4)
        right.addWidget(panel, 1)

        right_host = QWidget()
        right.setContentsMargins(0, 0, 6, 0)
        right_host.setLayout(right)
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.NoFrame)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        right_scroll.setWidget(right_host)
        columns.addWidget(right_scroll, 1)
        outer.addLayout(columns, 1)

        # Live status surface stays pinned at the bottom so the spinner/log is
        # always visible.
        self.status_view = LiveStatusView(self)
        outer.addWidget(self.status_view)

    # --- cascade ---------------------------------------------------------------

    def on_account_changed(self, index):
        """Account changed: on the paid tier that changes the legal GPUs."""
        if self._applying:
            return
        if not self.free_option_check.currentData():
            self._rebuild_gpu_options()
        self._apply_cascade()

    def _on_account_picked(self, _index):
        # `activated` fires only for a pick made by hand, never for our own
        # setCurrentIndex -- so from here on, leave the account where they put it.
        self._account_pinned = True

    def on_gpu_changed(self, index):
        if self._applying:
            return
        self._apply_cascade()

    def on_memory_changed(self, index):
        if self._applying:
            return
        self._apply_cascade()

    def on_gpu_count_changed(self, _value):
        # #GPUs feeds the CPU floor (2 cores/GPU), so recompute the cascade.
        if self._applying:
            return
        self._apply_cascade()

    def on_cpu_changed(self, _value):
        if self._applying:
            return
        self._update_status_line()
        self._update_advice()

    def on_time_changed(self, _index):
        if self._applying:
            return
        self._update_status_line()
        self._update_advice()

    def on_free_changed(self, _data=None):
        """Tier is the top of the cascade: it relabels the accounts (priority vs
        SUs), re-picks the best account for a free job, and rebuilds the GPU list."""
        if self._applying:
            return
        self._account_pinned = False
        self._populate_account_combo()
        self._rebuild_gpu_options()
        self._apply_cascade()

    def on_exclude_changed(self):
        # A tick is a flag. The store answers with `changed`, which refreshes the
        # summary and the advice (the picker's own rows are left alone while its
        # popup is open -- see _rebuild_exclude_options).
        self.flags.set_names(self.exclude_combo.checked_values())

    # --- bad nodes --------------------------------------------------------------

    def _avoided(self):
        """The nodes a launch is kept off: the flagged ones the cluster still has.

        A flag can outlive its node, and ``sbatch --exclude`` rejects a name it
        does not know -- which would fail every launch with a baffling error.
        """
        names = self.flags.names()
        nodes = (self.snapshot or {}).get('nodes')
        return [n for n in names if n in nodes] if nodes else names

    def _on_flags_changed(self):
        self._update_flag_button()
        self._update_more_label()
        self._rebuild_exclude_options()
        if self._applying:
            return
        self._update_status_line()
        self._update_advice()

    def _selected_node(self):
        node = (self.sessions.get(self.selected_job_id) or {}).get('node')
        return node if valid_node(node) and node != 'Unknown' else None

    def _update_flag_button(self):
        node = self._selected_node()
        self.flag_btn.setEnabled(node is not None)
        if node and self.flags.is_flagged(node):
            self.flag_btn.setText("Remove flag")
            self.flag_btn.setToolTip(f"{node} is {self.flags.describe(node)}. "
                                     "Click to let jobs use it again.")
        else:
            self.flag_btn.setText("Flag node as bad")
            self.flag_btn.setToolTip(
                f"Keep every future launch off {node}." if node else
                "Select a session that has a node to flag that node.")

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

    def _avoid_suspects(self):
        if self._suspects:
            node = self._suspects[0]
            count = (self.snapshot or {}).get('avoided', {}).get(node, 0)
            self.flags.flag(node, f"{count} other users' queued jobs avoid it")

    def _request(self):
        """The current form as a dict, or None while no account is chosen."""
        account = self.account_combo.currentData()
        choice = decode_choice(self.gpu_combo.currentData())
        if account is None or choice is None:
            return None
        family, gpu_type = choice
        use_free = bool(self.free_option_check.currentData())
        return {
            'account': account, 'family': family, 'gpu_type': gpu_type,
            'use_free': use_free,
            'gpu_count': self.gpu_count_spinbox.value() if gpu_type is not None else 0,
            'cpus': self.cpu_spinbox.value(),
            'memory': self.memory_combo.currentText(),
            'time_limit': self.time_combo.currentText(),
            'partition': hc.partition_for(account, gpu_type, use_free, family),
        }

    def _rebuild_exclude_options(self):
        """Repopulate the bad-node picker: flagged nodes first (always listed, so a
        flag can never be hidden), then your sessions' nodes, then the rest of the
        partition the form currently targets. Nodes that other users' queued jobs
        avoid say so."""
        checked = self.flags.names()
        avoided = (self.snapshot or {}).get('avoided', {})
        items, seen = [], set()

        def add(node, note=""):
            if node and node not in seen:
                seen.add(node)
                count = avoided.get(node, 0)
                if count >= 2 and not note:
                    note = f"  — {count} other users avoid it"
                items.append((f"{node}{note}", node))

        for node in checked:
            add(node, f"  — {self.flags.describe(node)}")
        for jid, info in sorted(self.sessions.items()):
            node = info.get('node')
            if node and node != 'Unknown':
                add(node, f"  — your job {jid}")
        req = self._request()
        if req is not None:
            for part in req['partition'].split(","):
                for node, state in self.partition_nodes.get(part, []):
                    add(node, "" if state in ("idle", "mixed", "allocated") else f"  ({state})")
        # Session polling lands here every few seconds: leave the rows alone when
        # nothing changed, and never swap them out from under an open popup.
        state = (items, checked)
        if state == self._exclude_items or self.exclude_combo.view().isVisible():
            return
        self._exclude_items = state
        self.exclude_combo.set_items(items, checked)
        self.exclude_combo.setEnabled(bool(items))

    def _default_gpu_choice(self, rows):
        """The row a fresh form opens on: the best GPU family the user's accounts
        point at (a GPU32 lab opens on GPU32, a CPU-only user on "No GPU")."""
        families = {hc.account_family(a['name']) for a in self.accounts}
        want = "gpu32" if "gpu32" in families else "gpu" if "gpu" in families else "cpu"
        for index, (_label, family, gpu_type) in enumerate(rows):
            if family == want and gpu_type in (hc.GPU_ANY, hc.GPU_NONE):
                return index
        return 0

    def _rebuild_gpu_options(self):
        """Repopulate the GPU dropdown for the tier and account.

        Paid: only the account's own family (a `…_GPU32` account offers
        L40S/RTX6000, a `…_GPU` account V100/A30/A100, a CPU account "No GPU").
        Free: every model, because free partitions accept every account. Either
        way each row carries its family, so a model can never be paired with the
        wrong partition. Built locally from the curated model; no SSH round-trip.
        """
        account = self.account_combo.currentData()
        previous = self.gpu_combo.currentData()
        self.gpu_combo.blockSignals(True)
        self.gpu_combo.clear()
        if account is None:
            self.gpu_combo.addItem("Select an account first", None)
            self.gpu_combo.setEnabled(False)
        else:
            rows = hc.tier_gpu_options(account, bool(self.free_option_check.currentData()))
            for text, family, gpu_type in rows:
                self.gpu_combo.addItem(text, encode_choice(family, gpu_type))
            self.gpu_combo.setEnabled(True)
            index = self.gpu_combo.findData(previous) if previous is not None else -1
            if index < 0:
                index = self._default_gpu_choice(rows)
            self.gpu_combo.setCurrentIndex(index)
        self.gpu_combo.blockSignals(False)

    def _apply_cascade(self):
        """Re-derive every downstream limit from the current selections.

        Runs top-to-bottom (tier -> account -> GPU -> #GPUs -> memory -> #CPUs ->
        run time); each field's range/enabled-state is recomputed so the form can
        only ever describe a configuration HPC3 will accept.
        """
        if self._applying:
            return
        self._applying = True
        try:
            account = self.account_combo.currentData()
            choice = decode_choice(self.gpu_combo.currentData()) if account is not None else None
            valid = account is not None and choice is not None
            family, gpu_type = choice if valid else (None, None)
            use_free = bool(self.free_option_check.currentData())

            # Number of GPUs: on only when a GPU is requested; max = per-node limit.
            if valid and gpu_type is not None:
                self.gpu_count_spinbox.setEnabled(True)
                self.gpu_count_spinbox.setMaximum(max(1, hc.max_gpus_for(gpu_type, family)))
            else:
                self.gpu_count_spinbox.setEnabled(False)

            # CPUs are DERIVED from memory: HPC3 caps RAM per core (partition-
            # specific -- 6 GB/core on gpu32, 9 on gpu), so memory fixes the exact
            # core count SLURM will allocate (and GPU jobs are floored at 2/GPU).
            # We pin the value to that, so the picker shows the real number (e.g.
            # 32G on gpu32 -> 6) instead of one SLURM would silently bump.
            if valid:
                mem_gb = hc.parse_mem_gb(self.memory_combo.currentText())
                gpu_count = self.gpu_count_spinbox.value() if gpu_type is not None else 0
                cpu_max = hc.max_cpus_for(gpu_type, family)
                cpu_need = min(hc.required_cpus(mem_gb, gpu_count, account, gpu_type,
                                                use_free, family), cpu_max)
                self.cpu_spinbox.setEnabled(True)
                self.cpu_spinbox.setMinimum(1)
                self.cpu_spinbox.setMaximum(cpu_max)
                self.cpu_spinbox.setValue(cpu_need)     # memory drives CPUs exactly
                self.cpu_spinbox.setMinimum(cpu_need)   # and you can't drop below it
            else:
                self.cpu_spinbox.setEnabled(False)

            # Run time: disable options beyond the tier cap (free 3d / paid 14d),
            # and snap the selection down if it now exceeds the cap.
            cap = hc.runtime_cap_hours(use_free)
            model = self.time_combo.model()
            best = None
            for i in range(self.time_combo.count()):
                ok = hc.parse_runtime_hours(self.time_combo.itemText(i)) <= cap + 1e-6
                item = model.item(i) if model is not None else None
                if item is not None:
                    item.setEnabled(ok)
                if ok:
                    best = i  # largest still-enabled option (the list ascends)
            if best is not None and hc.parse_runtime_hours(self.time_combo.currentText()) > cap + 1e-6:
                self.time_combo.setCurrentIndex(best)

            self.submit_btn.setEnabled(valid and self.vscode_manager is not None)
            self._rebuild_exclude_options()  # the target partition may have changed
            self._update_status_line()
            self._update_advice()
        finally:
            self._applying = False

    def _update_status_line(self):
        """A one-glance summary of what will actually be requested."""
        req = self._request()
        if req is None:
            self.status_label.setText("Pick an account to build a request.")
            return
        if req['gpu_type'] is None:
            what = "CPU only"
        elif req['gpu_type'] == hc.GPU_ANY:
            what = f"{req['gpu_count']} × any GPU"
        else:
            what = f"{req['gpu_count']} × {req['gpu_type']}"
        text = (f"<b>{req['partition'].replace(',', ' or ')}</b> · {what} · "
                f"{req['cpus']} CPUs · {req['memory']} · {req['time_limit']}")
        hours = hc.parse_runtime_hours(req['time_limit'])
        low, high = hc.su_cost_range(req['cpus'], req['gpu_type'], req['gpu_count'], hours,
                                     req['use_free'], req['family'])
        if req['use_free']:
            text += " · <b>free</b>"
        elif low == high:
            text += f" · up to <b>{high:,} SUs</b>"
        else:
            text += f" · up to <b>{low:,}–{high:,} SUs</b>"
        excluded = self._avoided()
        if excluded:
            text += f"<br>Kept off {', '.join(excluded)}"
        self.status_label.setText(text)

    # --- free-tier advice ------------------------------------------------------

    def _update_advice(self):
        """Refresh the priority / fit / run-time advice under the form."""
        req = self._request()
        if req is None or not req['use_free'] or self.snapshot is None:
            self.advice_card.setVisible(False)
            return
        self.advice_card.setVisible(True)
        self._update_priority_advice(req)
        self._update_fit_advice(req)
        self._update_suspect_advice(req)
        self._update_compare(req)
        hours = hc.parse_runtime_hours(req['time_limit'])
        self._set_line(self.time_dot, self.time_hint_label,
                       "A shorter run time helps: Slurm slots short jobs into gaps a "
                       "long one can't use." if hours > 12 else "")

    def _update_priority_advice(self, req):
        shares = self._fairshare()
        account = req['account']
        self.use_best_btn.hide()
        if account not in shares:
            self._set_line(self.priority_dot, self.priority_label, "")
            return
        priority = cv.free_priority(shares[account])
        partitions = req['partition'].split(",")
        ahead, competing = 0, 0
        for part in partitions:
            a, c = cv.jobs_ahead(self.snapshot.get('pending', []), part, priority)
            ahead, competing = max(ahead, a), max(competing, c)
        if not competing:
            standing = "nobody is waiting in this queue"
        elif ahead == 0:
            standing = f"ahead of all {competing} waiting jobs"
        else:
            standing = f"{ahead} of {competing} waiting jobs rank above you"
        best = self._best_free_account()
        if best and best != account and shares[best] > shares[account]:
            self._set_line(
                self.priority_dot, self.priority_label,
                f"<b>Queue priority ≈ {priority:,}</b> with {account} — {standing}. "
                f"<b>{best}</b> would give ≈ {cv.free_priority(shares[best]):,}.", "warn")
            self.use_best_btn.show()
        else:
            self._set_line(
                self.priority_dot, self.priority_label,
                f"<b>Queue priority ≈ {priority:,}</b> with {account} — {standing}.",
                "ok" if ahead == 0 else None)

    def _update_fit_advice(self, req):
        self.shrink_btn.hide()
        self._shrink_target = None
        if req['gpu_type'] is None:
            self._set_line(self.fit_dot, self.fit_label, "")
            return
        mem_gb = hc.parse_mem_gb(req['memory'])
        report = cv.fit_report(self.snapshot['nodes'], req['family'], req['gpu_type'],
                               req['gpu_count'], req['cpus'], mem_gb, use_free=True,
                               exclude=self._avoided())
        what = req['gpu_type'] or "GPU"
        plural = "s" if req['gpu_count'] != 1 else ""
        skipped = report['avoided_idle']
        aside = (f" ({skipped} more idle on a node you flagged.)" if skipped else "")
        if report['fits']:
            first = report['fits'][0]
            more = len(report['fits']) - 1
            self._set_line(
                self.fit_dot, self.fit_label,
                f"<b>Fits right now</b> on {first}" + (f" and {more} more" if more else "")
                + " — idle GPU, cores and memory to spare." + aside, "ok")
            return
        best = report['best']
        if best is None:
            idle = report['idle_gpus']
            if idle:
                why = (f"{idle} idle {what}{'s' if idle != 1 else ''}, but none on a node "
                       f"with {req['gpu_count']} free and spare cores")
            else:
                why = f"no idle {what} right now"
            self._set_line(self.fit_dot, self.fit_label,
                           f"<b>Will queue</b> — {why}.{aside}")
            return
        target = self._largest_memory_that_fits(best, req)
        self._set_line(
            self.fit_dot, self.fit_label,
            f"<b>Too big for what's idle.</b> {best['name']} has {req['gpu_count']} idle "
            f"{what}{plural} but only {best['cpus']} cores / {best['mem_gb']} GB left."
            + (f" {target} would start now." if target else "") + aside, "warn")
        if target:
            self._shrink_target = target
            self.shrink_btn.show()

    def _update_suspect_advice(self, req):
        """Point out a node in this request's pool that other users steer clear of."""
        self._suspects = []
        if req['gpu_type'] is not None:
            avoided = self.snapshot.get('avoided', {})
            pool = {n['name'] for n in cv.nodes_for(self.snapshot['nodes'], req['family'],
                                                    req['gpu_type'] or None)}
            self._suspects = [n for n in cv.suspect_nodes(avoided)
                              if n in pool and not self.flags.is_flagged(n)]
        if not self._suspects:
            self._set_line(self.suspect_dot, self.suspect_label, "")
            self.avoid_suspect_btn.hide()
            return
        # One at a time, most-avoided first: every flag shrinks the pool the job
        # can start on, so each one is its own decision.
        first = self._suspects[0]
        more = len(self._suspects) - 1
        self._set_line(
            self.suspect_dot, self.suspect_label,
            f"<b>{first} may be faulty</b> — {self.snapshot['avoided'][first]} other "
            f"users' queued jobs avoid it." + (f" ({more} more like it.)" if more else ""),
            "warn")
        self.avoid_suspect_btn.show()

    # --- which GPU starts soonest ----------------------------------------------

    def _comparison_rows(self, req):
        """This request re-cut for each GPU model: [(family, model, gpus, kwargs)].

        The size is kept (memory, run time, account, avoided nodes); the GPU count
        is clamped to what one node of that model has, and the cores follow the
        partition's memory-per-core rule, exactly as the form would derive them.
        """
        rows = []
        mem_gb = hc.parse_mem_gb(req['memory'])
        want = max(1, req['gpu_count'])
        for family in ("gpu32", "gpu"):
            for model in hc._FAMILY_MODEL_ORDER[family]:
                gpus = min(want, hc.max_gpus_for(model, family))
                cpus = min(hc.required_cpus(mem_gb, gpus, req['account'], model, True, family),
                           hc.max_cpus_for(model, family))
                rows.append((family, model, gpus, dict(
                    cpus=cpus, memory=req['memory'], gpu_type=model, gpu_count=gpus,
                    account=req['account'], time_limit=req['time_limit'], use_free=True,
                    exclude_nodes=self._avoided(), family=family)))
        return rows

    def _comparison_key(self, req):
        # Everything the estimates depend on except the GPU pick itself.
        return (req['account'], max(1, req['gpu_count']), req['memory'], req['time_limit'],
                tuple(self._avoided()))

    def _update_compare(self, req):
        usable = req['gpu_type'] is not None and self.insights is not None
        self.compare_btn.setVisible(usable)
        running = self._compare_worker is not None and self._compare_worker.isRunning()
        self.compare_btn.setEnabled(usable and not running)
        if not usable or self._comparison_key(req) != self._compare_key:
            self.compare_box.hide()      # the size changed: those estimates are stale
        elif self._compare_key is not None:
            self._mark_compared_choice(req)

    def compare_starts(self):
        """Ask Slurm when this size of job would start on each GPU model."""
        req = self._request()
        if req is None or self.insights is None or req['gpu_type'] is None:
            return
        rows = self._comparison_rows(req)
        try:
            commands = [build_sbatch_command(test_only=True, **kwargs)
                        for _family, _model, _gpus, kwargs in rows]
        except ValueError as e:
            self.show_error(str(e))
            return
        key = self._comparison_key(req)
        self.compare_btn.setEnabled(False)
        self.compare_btn.setText("Asking Slurm…")
        worker = SSHWorker(lambda rep: self.insights.estimate_starts(commands),
                           parent=self, label="compare")
        worker.succeeded.connect(lambda results: self._on_comparison(key, rows, results))
        worker.failed.connect(lambda message, hint: self._on_comparison(key, rows, None))
        self._compare_worker = worker
        worker.start()

    def _on_comparison(self, key, rows, results):
        self.compare_btn.setText("Check start times")
        self.compare_btn.setEnabled(True)
        while self.compare_row.count():
            item = self.compare_row.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        self._compare_rows = []
        self._compare_key = key
        if results is None:
            self.compare_row.addWidget(label("Could not reach Slurm for the estimates.",
                                             "muted", wrap=True), 1)
            self.compare_box.show()
            return
        merged = [(family, model, gpus, result)
                  for (family, model, gpus, _kw), result in zip(rows, results)]
        # Soonest first; anything Slurm would reject goes last.
        merged.sort(key=lambda r: (not r[3].get('ok'), r[3].get('start') or ""))
        for family, model, gpus, result in merged:
            # One tile per model; clicking it switches the form to that GPU.
            name = f"{gpus} × {model}" if gpus > 1 else model
            vram = f"{hc.GPU_MODELS[model]['vram_gb']} GB"
            if result.get('ok'):
                when = friendly_wait(result['start'])
                tip = (f"Slurm's estimate for this size on {model}: "
                       f"{friendly_time(result['start'])}"
                       + (f", on {result['node']}" if result.get('node') else "")
                       + ". Click to switch the form to it.")
            else:
                when = "won't run"
                tip = result.get('message') or "Slurm would reject this size here"
            tile = button(f"{name}\n{vram}\n{when}", "tile",
                          lambda _c=False, f=family, m=model: self._use_gpu(f, m))
            tile.setCheckable(True)
            tile.setEnabled(bool(result.get('ok')))
            tile.setToolTip(tip)
            self.compare_row.addWidget(tile, 1)
            self._compare_rows.append((family, model, tile, when))
        self.compare_box.show()
        req = self._request()
        if req is not None:
            self._update_compare(req)

    def _mark_compared_choice(self, req):
        """Highlight the tile for the GPU the form is on."""
        for family, model, tile, _when in self._compare_rows:
            tile.setChecked((req['family'], req['gpu_type']) == (family, model))
        self.compare_box.show()

    def _use_gpu(self, family, model):
        index = self.gpu_combo.findData(encode_choice(family, model))
        if index >= 0:
            self.gpu_combo.setCurrentIndex(index)
        req = self._request()
        if req is not None and self._compare_rows:
            self._mark_compared_choice(req)   # a click toggles the tile; put it right

    def _largest_memory_that_fits(self, room, req):
        """The biggest memory option whose derived core count fits ``room``."""
        fits = None
        for option in MEMORY_OPTIONS:
            mem_gb = hc.parse_mem_gb(option)
            cpus = hc.required_cpus(mem_gb, req['gpu_count'], req['account'],
                                    req['gpu_type'], True, req['family'])
            if mem_gb <= room['mem_gb'] and cpus <= room['cpus']:
                fits = option
        return fits

    def _shrink_to_fit(self):
        target = self._shrink_target
        if target:
            self.memory_combo.setCurrentText(target)   # CPUs follow via the cascade

    def _use_best_account(self):
        best = self._best_free_account()
        index = self.account_combo.findData(best) if best else -1
        if index >= 0:
            self._account_pinned = False
            self.account_combo.setCurrentIndex(index)

    # --- remembering the last request -----------------------------------------

    def _settings(self):
        return QSettings("MolloiLab", "HPC3 Launcher")

    def _remember_request(self, req):
        s = self._settings()
        s.setValue("sessions/free", "1" if req['use_free'] else "0")
        s.setValue("sessions/family", req['family'] or "")
        s.setValue("sessions/gpu_type", "none" if req['gpu_type'] is None else req['gpu_type'])
        s.setValue("sessions/gpu_count", int(req['gpu_count'] or 1))
        s.setValue("sessions/memory", req['memory'])
        s.setValue("sessions/time", req['time_limit'])

    def _restore_last_request(self):
        """Reopen the form on what was launched last time (tier, GPU, size)."""
        s = self._settings()
        if s.value("sessions/memory") is None:
            return
        self._applying = True
        try:
            use_free = s.value("sessions/free", "1") != "0"
            if use_free != bool(self.free_option_check.currentData()):
                self.free_option_check.setCurrentData(use_free)
                self._populate_account_combo()
                self._rebuild_gpu_options()
            raw = s.value("sessions/gpu_type", "")
            index = self.gpu_combo.findData(f"{s.value('sessions/family', '')}|{raw}")
            if index >= 0:
                self.gpu_combo.setCurrentIndex(index)
            try:
                self.gpu_count_spinbox.setValue(int(s.value("sessions/gpu_count", 1)))
            except (TypeError, ValueError):
                pass
            for combo, key in ((self.memory_combo, "sessions/memory"),
                               (self.time_combo, "sessions/time")):
                value = s.value(key)
                if value and combo.findText(value) >= 0:
                    combo.setCurrentText(value)
        finally:
            self._applying = False

    # --- submit / cancel -------------------------------------------------------

    @pyqtSlot()
    def submit_job(self):
        """Validate locally, then run the submit sequence on a worker thread."""
        if not self.vscode_manager:
            self.show_error("VSCode manager not initialized, unable to submit job")
            return

        # All validation below is local (no SSH) -- do it before going async.
        req = self._request()
        if req is None:
            self.show_error("Please select an account")
            return

        # The cascade keeps the form valid; this re-checks the account<->GPU pairing
        # as a backstop before a paid request reaches SLURM. (Free partitions take
        # any account, so there is nothing to pair.)
        if not req['use_free']:
            own = hc.account_family(req['account'])
            if hc.is_gpu_family(own) and req['gpu_type'] is None:
                self.show_error("This GPU account needs a GPU selected (pick 'Any GPU' or a model)")
                return
            if not hc.is_gpu_family(own) and req['gpu_type'] is not None:
                self.show_error("This is a CPU account — paid GPU partitions need a GPU account")
                return

        self._pending_submit = dict(
            cpus=req['cpus'], memory=req['memory'], gpu_type=req['gpu_type'],
            gpu_count=max(1, req['gpu_count']), account=req['account'],
            time_limit=req['time_limit'], use_free=req['use_free'], family=req['family'],
            exclude_nodes=self._avoided(),
        )
        self._remember_request(req)
        # Each launch is independent -- multiple sessions can run at once, so we
        # don't check for (or cancel) an existing one. Just submit.
        self._do_submit()

    def _do_submit(self):
        """Submit a new VSCode job on a worker thread."""
        cfg = self._pending_submit or {}
        self.status_view.start("Launching VSCode session", "running sbatch on HPC3")
        worker = SSHWorker(
            lambda rep: self.vscode_manager.submit_vscode_job(**cfg),
            parent=self, label="vscode-submit")
        worker.progress.connect(self.status_view.set_state)
        worker.log.connect(self.status_view.append_log)
        worker.failed.connect(self._submit_failed)
        worker.succeeded.connect(self._submit_done)
        worker.start()

    def _submit_done(self, _ok):
        self.status_view.finish_ok(
            "submitted — it appears on the right and turns Ready when the node starts")
        # Pick up its partition / priority / wait reason without waiting a minute.
        QTimer.singleShot(4000, self._sync_with_queue)

    def _submit_failed(self, message, hint=""):
        self.status_view.finish_error(message, hint)

    @pyqtSlot()
    def cancel_selected_session(self):
        """Cancel the session selected in the table (background; others keep running)."""
        job_id = self.selected_job_id
        if not self.vscode_manager or not job_id:
            self.show_error("Select a session to cancel.")
            return
        self.cancel_btn.setEnabled(False)
        self.status_view.start("Cancelling session", f"scancel {job_id}")

        def task(rep):
            ok = self.vscode_manager.cancel_job(job_id)  # also removes its SSH-config block
            return ok

        worker = SSHWorker(task, parent=self, label="vscode-cancel")
        worker.progress.connect(self.status_view.set_state)
        worker.log.connect(self.status_view.append_log)
        worker.failed.connect(self.show_error)

        def _done(ok):
            if ok:
                self.status_view.finish_ok(f"cancelled session {job_id}")
                self._upsert_session({'job_id': job_id, 'status': 'CANCELLED'})
            else:
                self.status_view.finish_error(f"could not cancel session {job_id}", "")
                self.cancel_btn.setEnabled(self.selected_job_id in self.sessions)

        worker.succeeded.connect(_done)
        worker.start()

    def show_error(self, error_msg, hint=""):
        """Surface an error non-modally in the status view (no modal-box spam)."""
        logger.error("%s %s", error_msg, hint)
        self.status_view.finish_error(str(error_msg), hint)

    @pyqtSlot(str, str)
    def on_ssh_config_added(self, job_id, hostname):
        """Handler function when SSH configuration is added to local file"""
        logger.info(f"SSH configuration added - Job: {job_id}, Host: {hostname}")

    @pyqtSlot(str)
    def on_ssh_config_removed(self, job_id):
        """Handler function when SSH configuration is removed from local file"""
        logger.info(f"SSH configuration removed - Job: {job_id}")
