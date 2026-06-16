#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
                           QTableWidget, QTableWidgetItem, QTabWidget,
                           QHeaderView, QStyle, QProgressBar, QSplitter, QFrame, QGridLayout)
from PyQt5.QtCore import Qt, QTimer, pyqtSlot, QDateTime, QThread, pyqtSignal
from PyQt5.QtGui import QFont, QColor, QIcon, QBrush
import logging
import time
from modules.node_status import NodeStatusManager
from modules.auth import HPC_SERVER, get_all_existing_users

# Configure logging
logger = logging.getLogger('NodeStatusWidget')

class RefreshWorker(QThread):
    """Thread for refreshing data in the background"""
    
    # Define signals
    finished = pyqtSignal()
    error = pyqtSignal(str)
    nodes_data = pyqtSignal(list)
    
    def __init__(self, node_manager):
        """Initialize refresh thread"""
        super().__init__()
        self.node_manager = node_manager
        self._stopped = False
    
    def run(self):
        """Thread execution function"""
        try:
            if self._stopped:
                return
                
            # Get node data
            nodes = self.node_manager.get_all_nodes()
            if nodes:
                self.nodes_data.emit(nodes)
            else:
                self.error.emit("Failed to retrieve node data")
        except Exception as e:
            logger.error(f"Failed to refresh data: {str(e)}")
            self.error.emit(f"Failed to refresh data: {str(e)}")
        finally:
            self.finished.emit()
    
    def stop(self):
        """Stop thread"""
        self._stopped = True
        if self.isRunning():
            self.wait(1000)
            if self.isRunning():
                self.terminate()

class NodeStatusWidget(QWidget):
    """Node status component, displays HPC cluster node information and availability"""
    
    def __init__(self, parent=None, username=None):
        super().__init__(parent)
        
        # User information
        self.username = username
        self.node_manager = None
        
        # Last refresh time
        self.last_refresh_time = None
        
        # Node data
        self.nodes_data = []
        
        # Refresh worker thread
        self.refresh_worker = None
        
        # Initialize node manager
        self.init_node_manager()
        
        # Initialize UI
        self.init_ui()
        
        # Timer to refresh time display
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.update_refresh_time)
        self.refresh_timer.start(10000)  # Every 10 seconds to update time display
        
        # Load data
        self.refresh_data()
    
    def init_node_manager(self):
        """Initialize node manager"""
        if not self.username:
            return
        
        # Get SSH key path
        users = get_all_existing_users()
        key_path = None
        
        for user in users:
            if user['username'] == self.username:
                key_path = user['key_path']
                break
        
        if not key_path:
            return
        
        # Create node manager
        self.node_manager = NodeStatusManager(
            hostname=HPC_SERVER,
            username=self.username,
            key_path=key_path
        )
        
        # Connect signals
        self.node_manager.nodes_updated.connect(self.update_nodes_data)
        self.node_manager.error_occurred.connect(self.show_error)
    
    def init_ui(self):
        """Initialize UI components"""
        main_layout = QVBoxLayout(self)
        
        # Top control bar
        control_layout = QHBoxLayout()
        
        # Refresh button with icon
        self.refresh_btn = QPushButton("Refresh Node Info")
        self.refresh_btn.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        self.refresh_btn.clicked.connect(self.refresh_data)
        control_layout.addWidget(self.refresh_btn)
        
        # Refresh status indicator
        self.refresh_indicator = QLabel("Ready")
        self.refresh_indicator.setStyleSheet("color: green;")
        control_layout.addWidget(self.refresh_indicator)
        
        control_layout.addStretch()
        
        # Overall statistics
        self.stats_label = QLabel("Total Nodes: 0 | CPU Usage: 0/0 | GPU Usage: 0/0")
        control_layout.addWidget(self.stats_label)
        
        control_layout.addStretch()
        
        # Refresh time display
        self.time_label = QLabel("Not refreshed yet")
        control_layout.addWidget(self.time_label)
        
        # Add control bar to main layout
        main_layout.addLayout(control_layout)
        
        # Create tabs
        tabs = QTabWidget()
        
        # GPU nodes tab (create GPU nodes tab first, place it in the first position)
        gpu_tab = QWidget()
        gpu_layout = QVBoxLayout(gpu_tab)
        
        self.gpu_nodes_table = QTableWidget()
        self.gpu_nodes_table.setColumnCount(5)
        self.gpu_nodes_table.setHorizontalHeaderLabels([
            "Node Name", "CPU Usage/Total", "Memory Usage", 
            "GPU Type", "GPU Usage/Total"
        ])
        self.gpu_nodes_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.gpu_nodes_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.gpu_nodes_table.setSortingEnabled(True)
        
        gpu_layout.addWidget(self.gpu_nodes_table)
        
        # All nodes tab
        all_nodes_tab = QWidget()
        all_layout = QVBoxLayout(all_nodes_tab)
        
        self.all_nodes_table = QTableWidget()
        self.all_nodes_table.setColumnCount(5)
        self.all_nodes_table.setHorizontalHeaderLabels([
            "Node Name", "CPU Usage/Total", "Memory Usage", 
            "GPU Type", "GPU Usage/Total"
        ])
        self.all_nodes_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.all_nodes_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.all_nodes_table.setSortingEnabled(True)
        
        all_layout.addWidget(self.all_nodes_table)
        
        # Add tabs, GPU nodes displayed first
        tabs.addTab(gpu_tab, "GPU Nodes")
        tabs.addTab(all_nodes_tab, "All Nodes")
        
        # Add tabs to main layout
        main_layout.addWidget(tabs)
        
        # Bottom status bar
        status_layout = QHBoxLayout()
        
        self.status_label = QLabel("Ready")
        status_layout.addWidget(self.status_label)
        
        # Add status bar to main layout
        main_layout.addLayout(status_layout)
    
    def update_refresh_time(self):
        """Update refresh time display"""
        if self.last_refresh_time:
            time_str = self.last_refresh_time.toString("yyyy-MM-dd hh:mm:ss")
            self.time_label.setText(f"Last refresh: {time_str}")
            
            # Calculate time since last refresh
            now = QDateTime.currentDateTime()
            secs = self.last_refresh_time.secsTo(now)
            
            # Set color based on time
            if secs < 60:  # Within 1 minute
                self.time_label.setStyleSheet("color: green;")
            elif secs < 300:  # Within 5 minutes
                self.time_label.setStyleSheet("color: orange;")
            else:  # More than 5 minutes
                self.time_label.setStyleSheet("color: red;")
    
    @pyqtSlot()
    def refresh_data(self):
        """Refresh node data"""
        if not self.node_manager:
            self.show_error("Node manager not set, unable to retrieve data")
            return
        
        # Update UI status
        self.refresh_btn.setEnabled(False)
        self.refresh_indicator.setText("Refreshing...")
        self.refresh_indicator.setStyleSheet("color: orange;")
        
        # Safely stop existing thread
        if self.refresh_worker and self.refresh_worker.isRunning():
            self.refresh_worker.stop()
        
        # Create new refresh thread
        self.refresh_worker = RefreshWorker(self.node_manager)
        
        # Connect signals
        self.refresh_worker.finished.connect(self.on_refresh_finished)
        self.refresh_worker.error.connect(self.show_error)
        self.refresh_worker.nodes_data.connect(self.update_nodes_data)
        
        # Start thread
        self.refresh_worker.start()
        
        # Update refresh time
        self.last_refresh_time = QDateTime.currentDateTime()
        self.update_refresh_time()
    
    def on_refresh_finished(self):
        """Callback function when refresh is finished"""
        # Update UI status
        self.refresh_btn.setEnabled(True)
        self.refresh_indicator.setText("Ready")
        self.refresh_indicator.setStyleSheet("color: green;")
    
    @pyqtSlot(list)
    def update_nodes_data(self, nodes_data):
        """Update node data"""
        if not nodes_data:
            return
        
        self.nodes_data = nodes_data
        
        # Update statistics
        self.update_stats()
        
        # Update all nodes table
        self.update_all_nodes_table()
        
        # Update GPU nodes table
        self.update_gpu_nodes_table()
    
    def update_stats(self):
        """Update statistics"""
        if not self.nodes_data:
            return
        
        # Calculate basic statistics
        total_nodes = len(self.nodes_data)
        
        total_cpus = sum(n['total_cpus'] for n in self.nodes_data)
        used_cpus = sum(n['alloc_cpus'] for n in self.nodes_data)
        
        gpu_nodes = [n for n in self.nodes_data if n['has_gpu']]
        total_gpus = sum(n['gpu_count'] for n in gpu_nodes)
        used_gpus = sum(n['used_gpus'] for n in gpu_nodes)
        
        # Update label
        stats_text = f"Total Nodes: {total_nodes} | CPU Usage: {used_cpus}/{total_cpus} | "
        stats_text += f"GPU Usage: {used_gpus}/{total_gpus}"
        self.stats_label.setText(stats_text)
    
    def update_all_nodes_table(self):
        """Update all nodes table"""
        self.all_nodes_table.setSortingEnabled(False)
        self.all_nodes_table.setRowCount(0)
        
        if not self.nodes_data:
            return
        
        # Add a row for each node
        for row, node in enumerate(self.nodes_data):
            self.all_nodes_table.insertRow(row)
            
            # Node name
            name_item = QTableWidgetItem(node['name'])
            # Set color of node name based on state
            self.set_color_by_state(name_item, node['state'])
            self.all_nodes_table.setItem(row, 0, name_item)
            
            # CPU Usage/Total
            cpu_text = f"{node['alloc_cpus']}/{node['total_cpus']}"
            cpu_item = QTableWidgetItem(cpu_text)
            self.set_color_by_usage(cpu_item, node['cpu_usage'])
            self.all_nodes_table.setItem(row, 1, cpu_item)
            
            # Memory Usage
            mem_text = f"{node['alloc_mem']}/{node['memory']}"
            mem_item = QTableWidgetItem(mem_text)
            self.set_color_by_usage(mem_item, node['memory_usage'])
            self.all_nodes_table.setItem(row, 2, mem_item)
            
            # GPU Type
            gpu_type = node['gpu_type'] if node['has_gpu'] else "N/A"
            self.all_nodes_table.setItem(row, 3, QTableWidgetItem(gpu_type))
            
            # GPU Usage/Total
            if node['has_gpu']:
                gpu_text = f"{node['used_gpus']}/{node['gpu_count']}"
                gpu_item = QTableWidgetItem(gpu_text)
                self.set_color_by_usage(gpu_item, node['gpu_usage'])
            else:
                gpu_text = "N/A"
                gpu_item = QTableWidgetItem(gpu_text)
            self.all_nodes_table.setItem(row, 4, gpu_item)
        
        self.all_nodes_table.setSortingEnabled(True)
    
    def update_gpu_nodes_table(self):
        """Update GPU nodes table"""
        self.gpu_nodes_table.setSortingEnabled(False)
        self.gpu_nodes_table.setRowCount(0)
        
        if not self.nodes_data:
            return
        
        # Filter GPU nodes
        gpu_nodes = [n for n in self.nodes_data if n['has_gpu']]
        
        # Add a row for each GPU node
        for row, node in enumerate(gpu_nodes):
            self.gpu_nodes_table.insertRow(row)
            
            # Node name
            name_item = QTableWidgetItem(node['name'])
            # Set color of node name based on state
            self.set_color_by_state(name_item, node['state'])
            self.gpu_nodes_table.setItem(row, 0, name_item)
            
            # CPU Usage/Total
            cpu_text = f"{node['alloc_cpus']}/{node['total_cpus']}"
            cpu_item = QTableWidgetItem(cpu_text)
            self.set_color_by_usage(cpu_item, node['cpu_usage'])
            self.gpu_nodes_table.setItem(row, 1, cpu_item)
            
            # Memory Usage
            mem_text = f"{node['alloc_mem']}/{node['memory']}"
            mem_item = QTableWidgetItem(mem_text)
            self.set_color_by_usage(mem_item, node['memory_usage'])
            self.gpu_nodes_table.setItem(row, 2, mem_item)
            
            # GPU Type
            self.gpu_nodes_table.setItem(row, 3, QTableWidgetItem(node['gpu_type']))
            
            # GPU Usage/Total
            gpu_text = f"{node['used_gpus']}/{node['gpu_count']}"
            gpu_item = QTableWidgetItem(gpu_text)
            self.set_color_by_usage(gpu_item, node['gpu_usage'])
            self.gpu_nodes_table.setItem(row, 4, gpu_item)
        
        self.gpu_nodes_table.setSortingEnabled(True)
    
    def set_color_by_usage(self, item, usage):
        """Set color based on usage rate"""
        if usage > 80:
            item.setForeground(QBrush(QColor(255, 0, 0)))  # Red
        elif usage > 60:
            item.setForeground(QBrush(QColor(255, 165, 0)))  # Orange
        else:
            item.setForeground(QBrush(QColor(0, 128, 0)))  # Green
    
    def set_color_by_state(self, item, state):
        """Colour a node row by its state.

        The states must match what NodeStatusManager._parse_nodes_info emits:
        "Error" / "Full" / "Partially Used" / "Idle". They used to be compared
        against Chinese literals the backend never produced, so every node fell
        through to the green "Idle" branch regardless of its real state.
        """
        if state == "Error":
            item.setForeground(QBrush(QColor(255, 0, 0)))        # Red
        elif state == "Full":
            item.setForeground(QBrush(QColor(255, 165, 0)))      # Orange
        elif state == "Partially Used":
            item.setForeground(QBrush(QColor(0, 0, 255)))        # Blue
        else:  # Idle
            item.setForeground(QBrush(QColor(0, 128, 0)))        # Green
    
    def show_error(self, error_msg):
        """Display error message"""
        self.refresh_indicator.setText(f"Error: {error_msg}")
        self.refresh_indicator.setStyleSheet("color: red;")
        logger.error(error_msg)
        
        # Enable refresh button
        self.refresh_btn.setEnabled(True)
    
    def closeEvent(self, event):
        """Close event"""
        # Stop all timers
        if hasattr(self, 'refresh_timer') and self.refresh_timer:
            self.refresh_timer.stop()
        
        # Stop refresh thread
        if self.refresh_worker and self.refresh_worker.isRunning():
            self.refresh_worker.stop()
        
        super().closeEvent(event) 