#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                           QGroupBox, QComboBox, QGridLayout, QSpinBox, QTextEdit,
                           QLineEdit, QMessageBox, QTabWidget, QScrollArea, QFrame,
                           QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView)
from PyQt5.QtCore import Qt, pyqtSlot, QTimer
from PyQt5.QtGui import QFont, QIcon

import logging
import time
from modules.vscode_helper import VSCodeManager
from modules.auth import HPC_SERVER, get_all_existing_users
from modules.balance import BalanceManager
from modules.node_status import NodeStatusManager
from core.ssh_session import SSHWorker
from ui.status_view import LiveStatusView
import os

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('VSCodeWidget')

class VSCodeWidget(QWidget):
    """VSCode remote configuration component, provides VSCode server setup and connection functionality"""
    
    def __init__(self, parent=None, username=None):
        super().__init__(parent)
        
        # User information
        self.username = username
        self.vscode_manager = None
        self.balance_manager = None
        self.node_manager = None
        
        # Account information
        self.accounts = []
        
        # GPU type information
        self.gpu_types = []
        
        # Running VSCode sessions, keyed by job id -> info dict (may carry 'config').
        # Multiple sessions can run at once; the table below lists them all.
        self.sessions = {}
        self.selected_job_id = None

        # Pending submit config; guard to suppress reentrant combo signals.
        self._pending_submit = None
        self._applying = False

        # Initialization complete flag
        self.initialization_complete = False
        
        # Initialize UI
        self.init_ui()
        
        # Use timer to delay initialization of other managers to avoid blocking UI at startup
        QTimer.singleShot(500, self.delayed_init_managers)
    
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
            self.status_label.setText("Error: Username not provided")
            return
        
        # Get SSH key path
        users = get_all_existing_users()
        key_path = None
        
        for user in users:
            if user['username'] == self.username:
                key_path = user['key_path']
                break
        
        if not key_path:
            self.status_label.setText(f"Error: SSH key for user {self.username} not found")
            return
        
        try:
            # Create manager objects in the main thread
            # Create VSCode manager
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
            
            # Create node status manager to get GPU type information
            self.node_manager = NodeStatusManager(
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
        """Fetch accounts + GPU types + any running session on a worker thread."""
        if not self.vscode_manager:
            return
        self.status_label.setText("Loading options…")
        self.status_view.start("Loading options", "reading your accounts and GPU types")
        self._init_worker = SSHWorker(self._fetch_options, parent=self, label="vscode-init")
        self._init_worker.progress.connect(self.status_view.set_state)
        self._init_worker.log.connect(self.status_view.append_log)
        self._init_worker.failed.connect(self._on_init_failed)
        self._init_worker.succeeded.connect(self._on_options_loaded)
        self._init_worker.start()

    def _fetch_options(self, rep):
        rep.state("loading", "reading your accounts")
        balance = self.balance_manager.get_user_balance(reporter=rep)
        accounts = []
        if balance and balance.get('accounts'):
            accounts = [{'name': a['name'], 'is_personal': a['is_personal'],
                         'available': a['available']} for a in balance['accounts']]
        rep.state("loading", "discovering GPU types")
        gpu_types = self._discover_gpu_types()
        rep.state("checking", "looking for a running VSCode session")
        running = self.vscode_manager.get_running_vscode_jobs()
        return {'accounts': accounts, 'gpu_types': gpu_types, 'running': running}

    def _discover_gpu_types(self):
        """GPU-type list for the combo; falls back to HPC3's known set on failure."""
        gpu_types = [{"name": "No GPU", "value": None},
                     {"name": "Any GPU (recommended)", "value": ""}]
        found = set()
        try:
            out = self.node_manager.execute_ssh_command('sinfo -o "%60N %10c %10m  %30f %10G" -e')
            for line in out.strip().split('\n')[1:]:
                if "gpu:" in line:
                    t = line.split("gpu:")[1].split(":")[0]
                    if t and t not in ("N/A", ""):
                        found.add(t)
        except Exception as e:
            logger.warning(f"GPU discovery failed, using defaults: {e}")
        for t in (sorted(found) if found else ["V100", "A30", "A100", "L40S"]):
            gpu_types.append({"name": f"{t} GPU (specific)", "value": t})
        return gpu_types

    def _on_init_failed(self, message, hint=""):
        self.status_view.finish_error(message, hint)
        self.status_label.setText("Could not load options")

    def _on_options_loaded(self, data):
        self.accounts = data['accounts']
        self.gpu_types = data['gpu_types']
        self._populate_account_combo()
        self._populate_gpu_combo()
        self._apply_account_gpu_constraints()
        self.status_label.setText("Ready")
        self.status_view.finish_ok("ready")

        # Seed the sessions table with every VSCode job already running.
        for job in (data['running'] or []):
            if job.get('status') == 'RUNNING' and job.get('job_id'):
                self.sessions[job['job_id']] = {
                    'job_id': job['job_id'], 'status': 'RUNNING',
                    'node': job.get('node', 'Unknown'),
                    'hostname': job.get('node', 'Unknown'),
                }
        self._render_sessions_table()
        # Auto-select the first session so its details show.
        if self.sessions and self.selected_job_id is None:
            self.sessions_table.selectRow(0)

    def _populate_account_combo(self):
        self.account_combo.blockSignals(True)
        self.account_combo.clear()
        self.account_combo.addItem("Select an account…", None)
        for account in self.accounts:
            label = f"{account['name']} (avail: {account['available']:,})"
            if account['is_personal']:
                label += " — personal"
            self.account_combo.addItem(label, account['name'])
        self.account_combo.setCurrentIndex(0)
        self.account_combo.blockSignals(False)

    def _populate_gpu_combo(self):
        self.gpu_combo.blockSignals(True)
        self.gpu_combo.clear()
        for g in self.gpu_types:
            self.gpu_combo.addItem(g["name"], g["value"])
        self.gpu_combo.blockSignals(False)

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
        if status in ('COMPLETED', 'CANCELLED', 'FAILED', 'TIMEOUT'):
            self.sessions.pop(jid, None)
            if self.selected_job_id == jid:
                self.selected_job_id = None
                self.job_info_text.clear()
                self.config_text.clear()
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

    def _render_sessions_table(self):
        self.sessions_table.blockSignals(True)
        self.sessions_table.setRowCount(0)
        for row, (jid, info) in enumerate(sorted(self.sessions.items())):
            self.sessions_table.insertRow(row)
            id_item = QTableWidgetItem(str(jid))
            id_item.setData(Qt.UserRole, jid)
            self.sessions_table.setItem(row, 0, id_item)
            host = info.get('hostname') or info.get('node') or '—'
            self.sessions_table.setItem(row, 1, QTableWidgetItem(str(host)))
            self.sessions_table.setItem(row, 2, QTableWidgetItem(info.get('status', '—')))
            ready = "Ready" if info.get('config') else "—"
            self.sessions_table.setItem(row, 3, QTableWidgetItem(ready))
            if jid == self.selected_job_id:
                self.sessions_table.selectRow(row)
        self.sessions_table.blockSignals(False)
        self.cancel_btn.setEnabled(self.selected_job_id in self.sessions)
        n = len(self.sessions)
        self.sessions_label.setText(f"Running VSCode sessions: {n}" if n else
                                    "Running VSCode sessions: none yet")

    def _on_session_selected(self):
        items = self.sessions_table.selectedItems()
        if not items:
            self.selected_job_id = None
            self.cancel_btn.setEnabled(False)
            return
        jid = self.sessions_table.item(items[0].row(), 0).data(Qt.UserRole)
        self.selected_job_id = jid
        self.cancel_btn.setEnabled(True)
        info = self.sessions.get(jid)
        if not info:
            return
        self._render_job_info(info)
        if info.get('config'):
            self._render_config(info)
        else:
            self.config_text.setPlainText("Fetching connection details…")
            self._fetch_config_for(jid)

    def _fetch_config_for(self, jid):
        """Parse one session's connection config on a worker thread, lazily."""
        worker = SSHWorker(lambda rep: self.vscode_manager._parse_vscode_config(jid),
                           parent=self, label="vscode-config")
        worker.succeeded.connect(
            lambda cfg: self._upsert_session({'job_id': jid, 'config': cfg,
                                              'hostname': cfg.get('hostname'),
                                              'port': cfg.get('port')}) if cfg else None)
        worker.start()

    def _render_job_info(self, info):
        """Render one session's details into the Job Information pane."""
        lines = [f"Job ID: {info.get('job_id', 'N/A')}",
                 f"Status: {info.get('status', 'N/A')}"]
        if info.get('node'):
            lines.append(f"Node: {info['node']}")
        lines.append("")
        lines.append("Resource Configuration:")
        lines.append(f"CPUs: {info.get('cpus', 'N/A')}")
        lines.append(f"Memory: {info.get('memory', 'N/A')}")
        if info.get('gpu_type'):
            lines.append(f"GPU Type: {info['gpu_type']}")
            lines.append(f"Number of GPUs: {info.get('gpu_count', 1)}")
        else:
            lines.append("GPU Type: No GPU")
        lines.append(f"Account: {info.get('account', 'N/A')}")
        lines.append(f"Run Time Limit: {info.get('time_limit', 'N/A')}")
        if 'use_free' in info:
            lines.append(f"Use Free Resources: {'Yes' if info['use_free'] else 'No'}")
        if 'submit_time' in info:
            lines.append("\nSubmitted: " +
                         time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(info['submit_time'])))
        self.job_info_text.setText("\n".join(lines))

    def _render_config(self, info):
        """Render one session's VSCode connection instructions."""
        config = info.get('config') or {}
        host = config.get('hostname', '?')
        text = (
            "## VSCode connection ready ##\n\n"
            "1. The SSH config has been written to ~/.ssh/config automatically.\n\n"
            "2. In VSCode: press F1 -> 'Remote-SSH: Connect to Host...' and pick:\n"
            f"     {host}\n\n"
            f"Host: {host}\n"
            f"User: {config.get('user', self.username)}\n"
            f"Port: {config.get('port', '?')}\n"
            f"Job ID: {info.get('job_id', 'N/A')}\n\n"
            "3. When done, select this session below and click \"Cancel Selected Session\".\n"
        )
        self.config_text.setText(text)
    
    def init_ui(self):
        """Initialize UI components"""
        # The whole page lives in a scroll area so the dense form can never overlap
        # itself when it's taller than the window -- e.g. in macOS large-text /
        # accessibility mode, where labels are taller and the content overflows.
        outer_layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_content = QWidget()
        main_layout = QVBoxLayout(scroll_content)

        # Add title
        title_label = QLabel("VSCode Remote Configuration")
        title_label.setFont(QFont('Arial', 16, QFont.Bold))
        main_layout.addWidget(title_label)

        # Upper part: configuration panel.
        config_widget = QWidget()
        config_layout = QVBoxLayout(config_widget)
        
        # Create resource configuration group
        resources_group = QGroupBox("Resource Configuration")
        resources_layout = QGridLayout(resources_group)
        
        # Number of CPUs
        cpu_label = QLabel("Number of CPUs:")
        self.cpu_spinbox = QSpinBox()
        self.cpu_spinbox.setMinimum(1)
        self.cpu_spinbox.setMaximum(128)
        self.cpu_spinbox.setValue(2)  # Default value changed to 2
        resources_layout.addWidget(cpu_label, 0, 0)
        resources_layout.addWidget(self.cpu_spinbox, 0, 1)
        
        # Memory size
        memory_label = QLabel("Memory Size:")
        self.memory_combo = QComboBox()
        for mem in ["4G", "8G", "16G", "32G", "64G", "128G"]:
            self.memory_combo.addItem(mem)
        self.memory_combo.setCurrentText("4G")  # Default value changed to 4G
        resources_layout.addWidget(memory_label, 1, 0)
        resources_layout.addWidget(self.memory_combo, 1, 1)
        
        # GPU type
        gpu_label = QLabel("GPU Type:")
        self.gpu_combo = QComboBox()
        self.gpu_combo.addItem("Loading…", None)
        self.gpu_combo.currentIndexChanged.connect(self.on_gpu_changed)
        resources_layout.addWidget(gpu_label, 2, 0)
        resources_layout.addWidget(self.gpu_combo, 2, 1)

        # GPU count
        gpu_count_label = QLabel("Number of GPUs:")
        self.gpu_count_spinbox = QSpinBox()
        self.gpu_count_spinbox.setMinimum(1)
        self.gpu_count_spinbox.setMaximum(8)
        self.gpu_count_spinbox.setValue(1)  # Default value
        self.gpu_count_spinbox.setEnabled(False)  # Disabled by default until GPU is selected
        resources_layout.addWidget(gpu_count_label, 3, 0)
        resources_layout.addWidget(self.gpu_count_spinbox, 3, 1)
        
        # Add resource configuration group to configuration layout
        config_layout.addWidget(resources_group)
        
        # Create job configuration group
        job_group = QGroupBox("Job Configuration")
        job_layout = QGridLayout(job_group)
        
        # Account
        account_label = QLabel("Account:")
        self.account_combo = QComboBox()
        self.account_combo.addItem("Loading...", "")
        self.account_combo.currentIndexChanged.connect(self.on_account_changed)
        job_layout.addWidget(account_label, 0, 0)
        job_layout.addWidget(self.account_combo, 0, 1)
        
        # Job time limit
        time_label = QLabel("Run Time:")
        self.time_combo = QComboBox()
        for time_limit in ["1:00:00", "2:00:00", "4:00:00", "8:00:00", "12:00:00", "24:00:00", "48:00:00"]:
            self.time_combo.addItem(time_limit)
        self.time_combo.setCurrentText("2:00:00")  # 2h default -- long enough to work, short enough not to silently drain SUs
        job_layout.addWidget(time_label, 1, 0)
        job_layout.addWidget(self.time_combo, 1, 1)
        
        # Free option -- default to Yes ("free" queues don't spend your SU balance).
        free_option_label = QLabel("Use Free Resources:")
        self.free_option_check = QComboBox()
        self.free_option_check.addItem("Yes — don't spend SUs (recommended)", True)
        self.free_option_check.addItem("No — use your allocation", False)
        self.free_option_check.setCurrentIndex(0)  # Free by default
        free_option_hint = QLabel("Free queues don't draw down your SU balance, but may wait longer. "
                                  "You still pick an account (it sets your partition).")
        free_option_hint.setStyleSheet("color: #888; font-size: 10px;")
        free_option_hint.setWordWrap(True)
        job_layout.addWidget(free_option_label, 2, 0)
        job_layout.addWidget(self.free_option_check, 2, 1)
        job_layout.addWidget(free_option_hint, 3, 0, 1, 2)
        
        # Add job configuration group to configuration layout
        config_layout.addWidget(job_group)
        
        # Create control buttons
        button_layout = QHBoxLayout()
        
        # Launch button -- each click starts a NEW session (multiple may run).
        self.submit_btn = QPushButton("Launch New Session")
        self.submit_btn.clicked.connect(self.submit_job)
        self.submit_btn.setEnabled(False)  # Default disabled until account is selected
        button_layout.addWidget(self.submit_btn)

        # Cancel the session selected in the table below.
        self.cancel_btn = QPushButton("Cancel Selected Session")
        self.cancel_btn.clicked.connect(self.cancel_selected_session)
        self.cancel_btn.setEnabled(False)  # Enabled when a session is selected
        button_layout.addWidget(self.cancel_btn)
        
        # Add button layout to configuration layout
        config_layout.addLayout(button_layout)
        
        # Config panel takes its natural height (no stretch).
        main_layout.addWidget(config_widget)
        
        # Lower part: sessions list + per-session detail tabs.
        result_container = QWidget()
        result_layout = QVBoxLayout(result_container)
        result_layout.setContentsMargins(0, 0, 0, 0)

        self.sessions_label = QLabel("Running VSCode sessions: none yet")
        self.sessions_label.setFont(QFont('Arial', 11, QFont.Bold))
        result_layout.addWidget(self.sessions_label)

        self.sessions_table = QTableWidget()
        self.sessions_table.setColumnCount(4)
        self.sessions_table.setHorizontalHeaderLabels(["Job ID", "Node / Host", "State", "Connect"])
        self.sessions_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.sessions_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.sessions_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.sessions_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.sessions_table.setMaximumHeight(150)
        self.sessions_table.itemSelectionChanged.connect(self._on_session_selected)
        result_layout.addWidget(self.sessions_table)

        result_widget = QTabWidget()

        # Job information tab
        self.job_info_widget = QWidget()
        job_info_layout = QVBoxLayout(self.job_info_widget)
        
        # Job status information
        self.job_info_text = QTextEdit()
        self.job_info_text.setReadOnly(True)
        self.job_info_text.setMinimumHeight(140)
        self.job_info_text.setPlaceholderText("Job status information will be displayed here after submission")
        job_info_layout.addWidget(self.job_info_text)
        
        # Configuration tab
        self.config_widget = QWidget()
        config_info_layout = QVBoxLayout(self.config_widget)
        
        # Configuration information
        self.config_text = QTextEdit()
        self.config_text.setReadOnly(True)
        self.config_text.setMinimumHeight(140)
        self.config_text.setPlaceholderText("VSCode remote connection configuration will be displayed here after job runs")
        config_info_layout.addWidget(self.config_text)
        
        # Add tabs
        result_widget.addTab(self.job_info_widget, "Job Information")
        result_widget.addTab(self.config_widget, "Connection Configuration")

        result_layout.addWidget(result_widget)

        # Sessions + detail tabs take all the remaining vertical space.
        main_layout.addWidget(result_container, 1)

        # Bottom status bar (inside the scroll content)
        self.status_label = QLabel("Initializing...")
        main_layout.addWidget(self.status_label)

        # Mount the scroll content; it expands to fill the page.
        scroll.setWidget(scroll_content)
        outer_layout.addWidget(scroll, 1)

        # Live status surface stays pinned at the bottom, outside the scroll, so the
        # spinner/log is always visible no matter how far the form is scrolled.
        self.status_view = LiveStatusView(self)
        outer_layout.addWidget(self.status_view)
    
    def on_account_changed(self, index):
        """Re-apply the account<->GPU rules whenever the account changes."""
        self._apply_account_gpu_constraints()

    def _apply_account_gpu_constraints(self):
        """Make only valid account+GPU combinations selectable.

        HPC3 rule: a GPU job needs an account whose name contains "gpu", and a
        non-GPU account can only run CPU jobs. So rather than letting the user pick
        a nonsense combo and erroring at submit time, we:
          * enable only the GPU options that match the chosen account,
          * snap the current selection to a valid one,
          * enable "Number of GPUs" only when a GPU is actually selected,
          * enable Submit only once the whole combination is valid.
        """
        if self._applying:
            return
        self._applying = True
        try:
            account = self.account_combo.currentData()
            valid_account = account is not None
            is_gpu_account = bool(valid_account and "gpu" in account.lower())

            model = self.gpu_combo.model()
            for i in range(self.gpu_combo.count()):
                value = self.gpu_combo.itemData(i)
                # "No GPU" (None) is valid only for non-GPU accounts; the GPU options
                # ("" = any, or a specific type) are valid only for GPU accounts.
                ok = (value is None and not is_gpu_account) or (value is not None and is_gpu_account)
                item = model.item(i) if model is not None else None
                if item is not None:
                    item.setEnabled(valid_account and ok)

            # Snap the selection to a valid option for this account.
            if valid_account:
                cur = self.gpu_combo.currentData()
                cur_ok = (cur is None and not is_gpu_account) or (cur is not None and is_gpu_account)
                if not cur_ok:
                    target = "" if is_gpu_account else None  # Any GPU, or No GPU
                    for i in range(self.gpu_combo.count()):
                        if self.gpu_combo.itemData(i) == target:
                            self.gpu_combo.setCurrentIndex(i)
                            break

            gpu_value = self.gpu_combo.currentData() if valid_account else None
            self.gpu_combo.setEnabled(valid_account)
            self.gpu_count_spinbox.setEnabled(valid_account and gpu_value is not None)
            self.submit_btn.setEnabled(valid_account)

            if not valid_account:
                self.status_label.setText("Select an account to enable submission")
            elif is_gpu_account:
                self.status_label.setText("GPU account — a GPU will be requested")
            else:
                self.status_label.setText("CPU account — GPU options are disabled")
        finally:
            self._applying = False
    

    @pyqtSlot()
    def submit_job(self):
        """Validate locally, then run the submit sequence on a worker thread."""
        if not self.vscode_manager:
            self.show_error("VSCode manager not initialized, unable to submit job")
            return

        # All validation below is local (no SSH) -- do it before going async.
        cpus = self.cpu_spinbox.value()
        memory = self.memory_combo.currentText()
        gpu_type = self.gpu_combo.currentData()
        gpu_count = self.gpu_count_spinbox.value()
        account = self.account_combo.currentData()
        time_limit = self.time_combo.currentText()
        use_free = self.free_option_check.currentData()

        if not account:
            self.show_error("Please select an account")
            return

        is_gpu_account = account and "gpu" in account.lower()
        is_requesting_gpu = gpu_type is not None  # None means no GPU
        if is_requesting_gpu and not is_gpu_account:
            self.show_error("Using GPU resources requires an account with GPU keyword")
            return
        if is_gpu_account and not is_requesting_gpu:
            self.show_error("Using GPU account requires selecting GPU resources")
            return

        self._pending_submit = dict(
            cpus=cpus, memory=memory, gpu_type=gpu_type, gpu_count=gpu_count,
            account=account, time_limit=time_limit, use_free=use_free,
        )
        # Each launch is independent -- multiple sessions can run at once, so we
        # don't check for (or cancel) an existing one. Just submit.
        self._do_submit()

    def _do_submit(self):
        """Submit a new VSCode job on a worker thread."""
        cfg = self._pending_submit or {}
        self.status_view.start("Launching VSCode session", "running sbatch on HPC3")
        self.status_label.setText("Launching a new VSCode session…")
        worker = SSHWorker(
            lambda rep: self.vscode_manager.submit_vscode_job(**cfg),
            parent=self, label="vscode-submit")
        worker.progress.connect(self.status_view.set_state)
        worker.log.connect(self.status_view.append_log)
        worker.failed.connect(self._submit_failed)
        worker.succeeded.connect(lambda _ok: self.status_view.finish_ok(
            "submitted — it will appear below and turn Ready when the node starts"))
        worker.start()

    def _submit_failed(self, message, hint=""):
        self.status_view.finish_error(message, hint)
        self.status_label.setText("Launch failed")
    
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
        self.status_label.setText("Error")
        self.status_view.finish_error(str(error_msg), hint)

    @pyqtSlot(str, str)
    def on_ssh_config_added(self, job_id, hostname):
        """Handler function when SSH configuration is added to local file"""
        logger.info(f"SSH configuration added - Job: {job_id}, Host: {hostname}")
        self.status_label.setText(f"VSCode connection configuration added - Host: {hostname}")
        
        # Add hint in job information, use setText to completely replace text, avoid using append
        job_info_text = self.job_info_text.toPlainText()
        if "SSH Configuration:" not in job_info_text:
            # Safely update text
            new_text = job_info_text + "\n\nSSH Configuration: Written to ~/.ssh/config"
            self.job_info_text.setText(new_text)

    @pyqtSlot(str)
    def on_ssh_config_removed(self, job_id):
        """Handler function when SSH configuration is removed from local file"""
        logger.info(f"SSH configuration removed - Job: {job_id}")
        self.status_label.setText(f"VSCode connection configuration removed")

    @pyqtSlot(int)
    def on_gpu_changed(self, index):
        """Only valid options are selectable, so just track the GPU-count enable."""
        if self._applying:
            return
        self.gpu_count_spinbox.setEnabled(self.gpu_combo.currentData() is not None)