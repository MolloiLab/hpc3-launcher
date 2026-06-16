#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import logging
import paramiko
import re
import time
import threading
from PyQt5.QtCore import QObject, pyqtSignal

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class NodeStatusManager(QObject):
    """Node status manager for querying HPC cluster node information"""
    
    # Signal definitions
    nodes_updated = pyqtSignal(list)       # Node information update signal
    error_occurred = pyqtSignal(str)       # Error signal
    
    def __init__(self, hostname, username, key_path=None, password=None):
        """
        Initialize node status manager
        
        Args:
            hostname: HPC hostname
            username: Username
            key_path: SSH key path
            password: SSH password
        """
        super().__init__()
        self.hostname = hostname
        self.username = username
        self.key_path = key_path
        self.password = password
        # RLock, not Lock: execute_ssh_command holds the lock and then calls
        # connect_ssh(), which locks again. A plain Lock isn't reentrant, so that
        # self-reacquire deadlocks the worker thread (lazy connect exposed this; the
        # old eager connect-in-__init__ hid it).
        self.lock = threading.RLock()
        
        # Cache SSH client
        self._ssh_client = None
        
        # Data cache to avoid frequent queries
        self.data_cache = {
            'last_refresh': 0,
            'refresh_interval': 60,  # Refresh interval (seconds)
            'nodes_data': []
        }
        # Connect lazily: the first execute_ssh_command() opens the connection on a
        # worker thread. Connecting here would block whatever thread built us --
        # at startup that's the UI thread, i.e. a multi-second freeze after login.
    
    def connect_ssh(self):
        """Connect to SSH server"""
        try:
            with self.lock:
                if self._ssh_client and self._ssh_client.get_transport() and self._ssh_client.get_transport().is_active():
                    logger.debug(f"[NodeStatusManager] Reusing existing SSH connection to {self.hostname} for node status operations")
                    return True
                
                # Create new SSH client
                logger.info(f"[NodeStatusManager] Establishing new SSH connection to {self.hostname} for node status operations")
                self._ssh_client = paramiko.SSHClient()
                self._ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                
                # Connect using key or password
                if self.key_path:
                    self._ssh_client.connect(
                        hostname=self.hostname,
                        username=self.username,
                        key_filename=self.key_path,
                        timeout=10
                    )
                elif self.password:
                    self._ssh_client.connect(
                        hostname=self.hostname,
                        username=self.username,
                        password=self.password,
                        timeout=10
                    )
                else:
                    raise ValueError("Must provide key path or password")
                
                logger.info(f"[NodeStatusManager] SSH connection to {self.hostname} established successfully")
                return True
        except Exception as e:
            logger.error(f"[NodeStatusManager] SSH connection to {self.hostname} failed: {e}")
            self.error_occurred.emit(f"SSH connection failed: {str(e)}")
            return False
    
    def _close_ssh_client(self):
        """Safely close SSH client connection"""
        with self.lock:
            if self._ssh_client:
                try:
                    logger.info(f"[NodeStatusManager] Closing SSH connection to {self.hostname}")
                    self._ssh_client.close()
                except Exception as e:
                    logger.error(f"[NodeStatusManager] Error closing SSH connection: {e}")
                    pass
                self._ssh_client = None

    def execute_ssh_command(self, command):
        """
        Execute SSH command and return results
        
        Args:
            command: Command to execute
            
        Returns:
            str: Command output
            
        Raises:
            Exception: Command execution error
        """
        with self.lock:
            if not self._ssh_client or not self._ssh_client.get_transport() or not self._ssh_client.get_transport().is_active():
                logger.info(f"[NodeStatusManager] SSH connection not active, reconnecting to {self.hostname}")
                if not self.connect_ssh():
                    raise Exception("Unable to connect to SSH server")
            
            try:
                logger.debug(f"[NodeStatusManager] Executing command on {self.hostname}: {command}")
                stdin, stdout, stderr = self._ssh_client.exec_command(command, timeout=30)
                output = stdout.read().decode('utf-8')
                error = stderr.read().decode('utf-8')
                
                if error and not output:
                    logger.error(f"[NodeStatusManager] Command error on {self.hostname}: {error}")
                    raise Exception(f"Command execution error: {error}")
                
                return output
            except Exception as e:
                logger.error(f"[NodeStatusManager] Command execution failed on {self.hostname}: {e}")
                # Try to reconnect
                self.connect_ssh()
                raise
    
    def get_all_nodes(self):
        """
        Get information for all nodes
        
        Returns:
            list: List of node information
        """
        current_time = time.time()
        # Check if cache is valid
        if (self.data_cache['nodes_data'] and 
            current_time - self.data_cache['last_refresh'] < self.data_cache['refresh_interval']):
            return self.data_cache['nodes_data']
        
        # Use sinfo command to get node information
        cmd = 'sinfo -N -O "NodeList:20,CPUsState:14,Memory:9,AllocMem:10,Gres:14,GresUsed:22"'
        try:
            output = self.execute_ssh_command(cmd)
            nodes_data = self._parse_nodes_info(output)
            
            # Update cache
            self.data_cache['nodes_data'] = nodes_data
            self.data_cache['last_refresh'] = current_time
            
            # Emit signal
            self.nodes_updated.emit(nodes_data)
            
            return nodes_data
        except Exception as e:
            self.error_occurred.emit(f"Failed to get node information: {str(e)}")
            return []
    
    def _parse_nodes_info(self, output):
        """
        Parse node information output
        
        Args:
            output: sinfo command output
            
        Returns:
            list: List of parsed node information
        """
        lines = output.strip().split('\n')
        if len(lines) < 2:  # Should have at least header line and one data line
            return []
        
        # Skip header line
        data_lines = lines[1:]
        nodes_dict = {}  # Use dictionary for deduplication
        
        for line in data_lines:
            # Split by spaces but preserve content in parentheses
            parts = re.findall(r'([^\s]+(?:\([^)]*\)[^\s]*)?|\S+)', line.strip())
            if len(parts) < 6:
                continue
            
            # Parse data
            node_name = parts[0]
            cpus_state = parts[1]
            memory = parts[2]
            alloc_mem = parts[3]
            gres = parts[4]
            gres_used = parts[5]
            
            # Parse CPU state (allocated/idle/offline/total)
            cpu_match = re.match(r'(\d+)/(\d+)/(\d+)/(\d+)', cpus_state)
            if cpu_match:
                alloc_cpus = int(cpu_match.group(1))
                idle_cpus = int(cpu_match.group(2))
                other_cpus = int(cpu_match.group(3))
                total_cpus = int(cpu_match.group(4))
            else:
                alloc_cpus = idle_cpus = other_cpus = total_cpus = 0
            
            # Parse GPU information
            gpu_type = ""
            gpu_count = 0
            used_gpus = 0
            
            if gres != "(null)":
                gpu_match = re.search(r'gpu:([^:]+):(\d+)', gres)
                if gpu_match:
                    gpu_type = gpu_match.group(1)
                    gpu_count = int(gpu_match.group(2))
            
            if gres_used != "(null)":
                # GPU usage format: gpu:TYPE:COUNT(IDX:indices)
                gpu_used_match = re.search(r'gpu:[^:]+:\d+\(IDX:([^)]*)\)', gres_used)
                if gpu_used_match:
                    indices = gpu_used_match.group(1)
                    if indices == "N/A":
                        used_gpus = 0
                    else:
                        # Calculate number of used GPUs (comma-separated indices)
                        used_gpus = len(indices.split('-')) if '-' in indices else len(indices.split(','))
            
            # Convert memory data to GB format
            memory_gb = self._convert_to_gb(memory)
            alloc_mem_gb = self._convert_to_gb(alloc_mem)
            
            # Calculate node usage
            cpu_usage = (alloc_cpus / total_cpus * 100) if total_cpus > 0 else 0
            memory_usage = (float(alloc_mem) / float(memory) * 100) if float(memory) > 0 else 0
            gpu_usage = (used_gpus / gpu_count * 100) if gpu_count > 0 else 0
            
            # Determine node state
            if other_cpus > 0:
                state = "Error"
            elif alloc_cpus == total_cpus:
                state = "Full"
            elif alloc_cpus > 0:
                state = "Partially Used"
            else:
                state = "Idle"
            
            # Create node data
            node = {
                'name': node_name,
                'alloc_cpus': alloc_cpus,
                'idle_cpus': idle_cpus,
                'other_cpus': other_cpus,
                'total_cpus': total_cpus,
                'memory': memory_gb,
                'alloc_mem': alloc_mem_gb,
                'has_gpu': gres != "(null)",
                'gpu_type': gpu_type,
                'gpu_count': gpu_count,
                'used_gpus': used_gpus,
                'cpu_usage': cpu_usage,
                'memory_usage': memory_usage,
                'gpu_usage': gpu_usage,
                'state': state
            }
            
            # Use node name as key, retain more resource records when merging duplicate nodes
            if node_name in nodes_dict:
                existing_node = nodes_dict[node_name]
                # If new node has GPU but existing node doesn't, replace
                if node['has_gpu'] and not existing_node['has_gpu']:
                    nodes_dict[node_name] = node
                # If both are the same or new node doesn't have GPU, retain existing node information
            else:
                nodes_dict[node_name] = node
        
        # Return deduplicated node list
        return list(nodes_dict.values())
    
    def _convert_to_gb(self, mem_str):
        """Convert memory string to GB format
        
        Args:
            mem_str: Memory string, e.g., "192000" represents 192000MB
            
        Returns:
            str: Formatted GB string, e.g., "187.5GB"
        """
        try:
            # Convert string to integer
            mem_mb = int(mem_str)
            # Convert to GB
            mem_gb = mem_mb / 1024.0
            # Format as string
            if mem_gb >= 100:
                # Large memory displayed as integer
                return f"{int(mem_gb)}GB"
            elif mem_gb >= 10:
                # Medium memory displayed as one decimal place
                return f"{mem_gb:.1f}GB"
            else:
                # Small memory displayed as two decimal places
                return f"{mem_gb:.2f}GB"
        except (ValueError, TypeError):
            return mem_str
    
    def refresh_nodes(self):
        """
        Force refresh node data
        """
        # Clear cache timestamp, force refresh
        self.data_cache['last_refresh'] = 0
        return self.get_all_nodes()
    
    def get_nodes_by_type(self):
        """
        Get nodes by type
        
        Returns:
            dict: Node dictionary grouped by type
        """
        nodes = self.get_all_nodes()
        
        # Group by type
        grouped = {
            'cpu_nodes': [],
            'gpu_nodes': []
        }
        
        for node in nodes:
            if node['has_gpu']:
                grouped['gpu_nodes'].append(node)
            else:
                grouped['cpu_nodes'].append(node)
        
        return grouped
    
    def get_nodes_stats(self):
        """
        Get node statistics
        
        Returns:
            dict: Node statistics
        """
        nodes = self.get_all_nodes()
        
        total_nodes = len(nodes)
        used_nodes = sum(1 for n in nodes if n['alloc_cpus'] > 0)
        
        total_cpus = sum(n['total_cpus'] for n in nodes)
        used_cpus = sum(n['alloc_cpus'] for n in nodes)
        
        total_gpus = sum(n['gpu_count'] for n in nodes if n['has_gpu'])
        used_gpus = sum(n['used_gpus'] for n in nodes if n['has_gpu'])
        
        # Calculate utilization
        node_usage = (used_nodes / total_nodes * 100) if total_nodes > 0 else 0
        cpu_usage = (used_cpus / total_cpus * 100) if total_cpus > 0 else 0
        gpu_usage = (used_gpus / total_gpus * 100) if total_gpus > 0 else 0
        
        return {
            'total_nodes': total_nodes,
            'used_nodes': used_nodes,
            'total_cpus': total_cpus,
            'used_cpus': used_cpus,
            'total_gpus': total_gpus,
            'used_gpus': used_gpus,
            'node_usage': node_usage,
            'cpu_usage': cpu_usage,
            'gpu_usage': gpu_usage
        }
    
    def __del__(self):
        """Destructor, ensure SSH connection is closed"""
        if hasattr(self, '_ssh_client') and self._ssh_client:
            try:
                self._ssh_client.close()
            except Exception as e:
                logging.error(f"Failed to close SSH connection: {str(e)}") 