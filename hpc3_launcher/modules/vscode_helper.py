#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import logging
import paramiko
import threading
import time
import os
from PyQt5.QtCore import QObject, pyqtSignal, QThread, Qt
from modules.hpc3_constraints import partition_for, account_family

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def _config_block_re(job_id=r".*?"):
    """
    Regex matching one of *our* managed SSH-config blocks in ~/.ssh/config.

    Matches both the new "HPC3 Launcher VSCode" marker and the legacy
    "HPC App VSCode Configuration" marker, so cleanup also reaps blocks the old
    app wrote. (The old writer/cleanup used mismatched markers -- one said
    "Configuration", the other "Config" -- so stale blocks never got removed.)
    """
    tag = r"HPC(?:3 Launcher| App) VSCode(?: Configuration)?"
    return re.compile(
        rf"\n*# === BEGIN {tag} \(JobID: {job_id}\) ===.*?"
        rf"# === END {tag} \(JobID: {job_id}\) ===",
        re.DOTALL,
    )


class VSCodeManager(QObject):
    """
    VSCode server manager responsible for submitting and managing VSCode server jobs
    """
    
    # Signal definitions
    vscode_job_submitted = pyqtSignal(dict)  # Job submission success signal
    vscode_job_status_updated = pyqtSignal(dict)  # Job status update signal
    vscode_config_ready = pyqtSignal(dict)  # Configuration ready signal
    error_occurred = pyqtSignal(str)  # Error signal
    ssh_config_added = pyqtSignal(str, str)  # SSH config added signal (job_id, hostname)
    ssh_config_removed = pyqtSignal(str)  # SSH config removed signal (job_id)
    
    def __init__(self, hostname, username, key_path=None, password=None):
        """
        Initialize VSCode manager
        
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
        self.lock = threading.Lock()  # Thread lock to ensure SSH connection safety
        
        # Cache SSH client
        self._ssh_client = None
        
        # Current running VSCode job information
        self.current_job = None
        
        # Track jobs with written config
        self.config_written_jobs = set()

        # Connect lazily -- the first SSH call (on a worker thread) opens the
        # connection. Connecting in __init__ would block the UI thread that builds
        # this manager, freezing the window right after login.
    
    def connect_ssh(self):
        """Connect to SSH server"""
        try:
            if self._ssh_client and self._ssh_client.get_transport() and self._ssh_client.get_transport().is_active():
                logger.debug(f"[VSCodeManager] Reusing existing SSH connection to {self.hostname} for VSCode server operations")
                return True
            
            # If there is an old connection, close it first
            if self._ssh_client:
                try:
                    logger.debug(f"[VSCodeManager] Closing old SSH connection")
                    self._ssh_client.close()
                except:
                    pass
            
            # Create a new SSH client
            logger.info(f"[VSCodeManager] Establishing new SSH connection to {self.hostname} for VSCode server operations")
            self._ssh_client = paramiko.SSHClient()
            self._ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            # Connect using key or password
            if self.key_path:
                self._ssh_client.connect(
                    hostname=self.hostname,
                    username=self.username,
                    key_filename=self.key_path,
                    timeout=15,
                    look_for_keys=False,
                    allow_agent=False
                )
            elif self.password:
                self._ssh_client.connect(
                    hostname=self.hostname,
                    username=self.username,
                    password=self.password,
                    timeout=15,
                    look_for_keys=False,
                    allow_agent=False
                )
            else:
                error_msg = "Key path or password must be provided"
                logger.error(f"[VSCodeManager] {error_msg}")
                self.error_occurred.emit(error_msg)
                return False
            
            logger.info(f"[VSCodeManager] SSH connection to {self.hostname} established successfully")
            return True
        except Exception as e:
            error_msg = f"SSH connection failed: {str(e)}"
            logger.error(f"[VSCodeManager] SSH connection to {self.hostname} failed: {e}")
            self.error_occurred.emit(error_msg)
            return False
    
    def _close_ssh_client(self):
        """Safely close SSH client connection"""
        if self._ssh_client:
            try:
                logger.debug(f"[VSCodeManager] Closing SSH connection to {self.hostname}")
                self._ssh_client.close()
            except Exception as e:
                logger.error(f"[VSCodeManager] Error closing SSH connection: {e}")
                pass
            self._ssh_client = None
    
    def execute_ssh_command(self, command):
        """
        Execute SSH command and return result
        
        Args:
            command: Command to execute
            
        Returns:
            str: Output of the command
        """
        try:
            # Ensure there is a connection
            if not self._ssh_client or not self._ssh_client.get_transport() or not self._ssh_client.get_transport().is_active():
                logger.info(f"[VSCodeManager] SSH connection not active, reconnecting to {self.hostname}")
                if not self.connect_ssh():
                    raise Exception("Unable to connect to SSH server")
            
            # Execute command
            logger.debug(f"[VSCodeManager] Executing command on {self.hostname}: {command}")
            stdin, stdout, stderr = self._ssh_client.exec_command(command, timeout=30)
            output = stdout.read().decode('utf-8')
            error = stderr.read().decode('utf-8')
            
            # If there is an error and no output, raise an exception
            if error and not output:
                logger.error(f"[VSCodeManager] Command error on {self.hostname}: {error}")
                raise Exception(f"Command execution error: {error}")
            
            return output
        except Exception as e:
            logger.error(f"[VSCodeManager] Command execution failed on {self.hostname}: {e}")
            # Attempt to reconnect
            self.connect_ssh()
            raise Exception(f"Command execution failed: {str(e)}")
    
    def submit_vscode_job(self, cpus=2, memory="4G", gpu_type=None, gpu_count=1, account=None, time_limit="2:00:00", use_free=False):
        """
        Submit VSCode job to HPC

        Args:
            cpus: Number of CPU cores
            memory: Memory size
            gpu_type: GPU type, such as V100, A30, etc.
                     None means no GPU
                     Empty string means any GPU
            gpu_count: Number of GPUs to request (default: 1)
            account: Billing account
            time_limit: Time limit in HH:MM:SS format
            use_free: Whether to use free resources

        Returns:
            bool: Whether the submission was successful
        """
        if not account:
            raise ValueError("Billing account must be specified")
        
        # Connect to SSH
        if not self.connect_ssh():
            raise Exception("Unable to connect to SSH server")
        
        try:
            # Build sbatch command
            cmd = "sbatch"
            
            # Add resource request parameters
            cmd += f" --cpus-per-task={cpus}"
            cmd += f" --mem={memory}"
            cmd += f" --time={time_limit}"
            cmd += f" --account={account}"
            
            # A GPU account (suffix `gpu`/`gpu32`) but no GPU type chosen -> request
            # "any GPU" so the GPU account isn't accidentally used CPU-only.
            account_contains_gpu = account_family(account) in ("gpu", "gpu32")
            if gpu_type is None and account_contains_gpu:
                gpu_type = ""  # "" sentinel -> any GPU of the account's family

            # Partition is derived from the curated HPC3 model (hpc3_constraints):
            # a specific GPU model pins the family (so RTX6000 -> gpu32, not gpu),
            # otherwise the account suffix decides; `use_free` then picks the free
            # vs paid partition within that family. This replaces the old hardcoded
            # "L40S -> gpu32, everything else -> gpu" rule that mis-routed RTX6000
            # and failed with "Requested node configuration is not available".
            cmd += f" -p {partition_for(account, gpu_type, use_free)}"

            # GPU resource request (only when a GPU was actually requested).
            if gpu_type == "":      # any GPU of the family
                cmd += f" --gres=gpu:{gpu_count}"
            elif gpu_type is not None:  # specific model, e.g. gpu:RTX6000:1
                cmd += f" --gres=gpu:{gpu_type}:{gpu_count}"
            
            # Add VSCode script path
            cmd += " /opt/rcic/scripts/vscode-sshd.sh"
            
            # Submit job
            logger.info(f"Submit job command: {cmd}")
            output = self.execute_ssh_command(cmd)
            logger.info(f"Submit job output: {output}")
            
            # Parse job ID
            job_id = None
            if output:
                match = re.search(r'Submitted batch job (\d+)', output)
                if match:
                    job_id = match.group(1)
            
            if not job_id:
                error_msg = f"Job submission failed, unable to get job ID"
                if output and "error" in output.lower():
                    error_msg += f"\nSlurm error: {output}"
                    # Hints for the common GPU account/partition mismatches. The
                    # cascading UI should prevent these, but surface guidance if
                    # SLURM still rejects the request.
                    if gpu_type:  # a specific GPU model was requested
                        fam = "gpu32" if account_family(account) == "gpu32" else "gpu"
                        if "not available" in output.lower():
                            error_msg += (f"\n\nNote: {gpu_type} GPUs live in the {fam} partition. "
                                          f"Make sure the GPU type matches your account "
                                          f"(a '…_GPU32' account for L40S/RTX6000, a '…_GPU' account "
                                          f"for V100/A30/A100), and that the count/CPUs/memory fit one node.")
                        if "invalid account" in output.lower() or "account/partition" in output.lower():
                            error_msg += (f"\n\nAccount issue: the {fam} partition needs a matching "
                                          f"'…_{fam.upper()}' account.")
                            error_msg += f"\n• Try enabling 'Use Free Resources' (free-{fam} partition)"
                            error_msg += f"\n• Or ask your faculty advisor to request a {fam.upper()} account"
                            error_msg += f"\n• Or select 'Any GPU (recommended)'"
                else:
                    error_msg += f"\nOutput: {output}"
                raise Exception(error_msg)
            
            # Determine the GPU type label to show in the UI.
            display_gpu_type = None
            if gpu_type is not None:
                if gpu_type == "":
                    display_gpu_type = "Any GPU"
                else:
                    display_gpu_type = f"{gpu_type} GPU"
            elif account_contains_gpu:
                display_gpu_type = "Any GPU (auto-selected)"

            # Record job information
            job_info = {
                'job_id': job_id,
                'status': 'PENDING',
                'cpus': cpus,
                'memory': memory,
                'gpu_type': display_gpu_type,
                'gpu_count': gpu_count if gpu_type is not None else 0,  # Record GPU count
                'account': account,
                'time_limit': time_limit,
                'submit_time': time.time(),
                'use_free': use_free,  # Record whether free resources are used
                'command': cmd,  # Record submission command
                'script_path': "/opt/rcic/scripts/vscode-sshd.sh"  # Use system script path
            }
            
            # Emit signal
            self.vscode_job_submitted.emit(job_info)
            
            # Start polling thread
            self._start_poll_job_status(job_id)
            
            return True
        
        except Exception as e:
            logger.error(f"Submit VSCode job failed: {e}")
            raise
    
    def wait_for_job_and_get_config(self, job_id):
        """
        Wait for job to run and get configuration information
        
        Args:
            job_id: Job ID
        """
        # Start a thread to monitor job status
        threading.Thread(target=self._monitor_job_status, args=(job_id,), daemon=True).start()
    
    def _monitor_job_status(self, job_id):
        """
        Monitor job status and get configuration information when the job is running
        
        Args:
            job_id: Job ID
        """
        try:
            # Wait up to 60 minutes
            max_wait_time = 60 * 60
            start_time = time.time()
            
            while time.time() - start_time < max_wait_time:
                # Check job status
                cmd = f"squeue -j {job_id} -h -o '%T %N'"
                try:
                    output = self.execute_ssh_command(cmd)
                    output = output.strip()
                    
                    if not output:
                        # Job may have ended
                        logger.warning(f"Job {job_id} may have ended, unable to get status")
                        self.current_job['status'] = 'COMPLETED'
                        self.vscode_job_status_updated.emit(self.current_job)
                        return
                    
                    # Parse status and node
                    parts = output.split()
                    if len(parts) >= 1:
                        status = parts[0]
                        node = parts[1] if len(parts) > 1 else "Not assigned"
                        
                        # Update job information
                        self.current_job['status'] = status
                        self.current_job['node'] = node
                        
                        # Send status update signal
                        self.vscode_job_status_updated.emit(self.current_job)
                        
                        # If the job is running, get configuration information
                        if status == 'RUNNING':
                            # Get job output file to parse configuration information
                            config_info = self._parse_vscode_config(job_id)
                            if config_info:
                                self.current_job['config'] = config_info
                                self.current_job['hostname'] = config_info.get('hostname')
                                self.current_job['port'] = config_info.get('port')
                                # Send configuration ready signal
                                self.vscode_config_ready.emit(self.current_job)
                                return
                except Exception as e:
                    logger.error(f"Error getting job status: {str(e)}")
                
                # Check every 10 seconds
                time.sleep(10)
            
            # Timeout
            logger.warning(f"Waiting for job {job_id} to run timed out")
            self.error_occurred.emit(f"Waiting for job {job_id} to run timed out, please check job status")
        except Exception as e:
            logger.error(f"Error monitoring job status: {str(e)}")
            self.error_occurred.emit(f"Error monitoring job status: {str(e)}")
    
    def _parse_vscode_config(self, job_id):
        """
        Parse VSCode configuration information
        
        Args:
            job_id: Job ID
            
        Returns:
            dict: Configuration information dictionary
        """
        try:
            # Get job output file
            cmd = f"cat vscode-sshd-{job_id}.out 2>/dev/null || echo 'Configuration file not found'"
            output = self.execute_ssh_command(cmd)
            
            logger.info(f"VSCode configuration file content:\n{output}")
            
            # If configuration file not found
            if "Configuration file not found" in output:
                # Query job information
                job_info = self.get_job_status(job_id)
                node = job_info.get('node') if job_info else None
                
                if node:
                    # If there is a node, construct basic configuration
                    config = {
                        'hostname': node,
                        'port': '22',  # Use default SSH port
                        'user': self.username,
                        'ssh_config': f"""Host {node}
  HostName {node}
  ProxyJump {self.username}@{self.hostname}
  User {self.username}
  UserKnownHostsFile /dev/null
  StrictHostKeyChecking no"""
                    }
                    logger.info(f"Construct configuration using node information: {config}")
                    return config
                else:
                    logger.warning(f"Unable to get node information for job {job_id}")
                    return None
            
            # Parse hostname and port
            hostname_match = re.search(r'HostName\s+(\S+)', output)
            port_match = re.search(r'Port\s+(\d+)', output)
            
            hostname = None
            port = None
            
            if hostname_match:
                hostname = hostname_match.group(1)
            else:
                # Try to find hostname from node line
                node_match = re.search(r'Node:\s+(\S+)', output)
                if node_match:
                    hostname = node_match.group(1)
            
            if port_match:
                port = port_match.group(1)
            else:
                # Default port
                port = "22"
            
            if not hostname:
                logger.warning(f"Unable to parse hostname from output: {output}")
                return None
            
            # Build configuration information
            config = {
                'hostname': hostname,
                'port': port,
                'user': self.username,
                'ssh_config': f"""Host {hostname}
  HostName {hostname}
  Port {port}
  ProxyJump {self.username}@{self.hostname}
  User {self.username}
  UserKnownHostsFile /dev/null
  StrictHostKeyChecking no"""
            }
            
            logger.info(f"Parsed VSCode configuration information: {config}")
            return config
        except Exception as e:
            logger.error(f"Error parsing VSCode configuration information: {str(e)}")
            return None
    
    def get_job_status(self, job_id):
        """
        Get job status
        
        Args:
            job_id: Job ID
        
        Returns:
            dict: Job status information
        """
        if not job_id:
            return None
        
        try:
            # Connect SSH
            if not self.connect_ssh():
                raise Exception("Unable to connect to SSH server")
            
            # Execute squeue command to query job
            cmd = f"squeue -j {job_id} -o '%j|%i|%T|%N|%C|%m|%l' -h"
            output = self.execute_ssh_command(cmd)
            
            # If there is no output, the job may have ended
            if not output or not output.strip():
                # Query sacct to get information of completed jobs
                sacct_cmd = f"sacct -j {job_id} -o JobName,JobID,State,NodeList,NCPUS,ReqMem,Timelimit -n -P"
                sacct_output = self.execute_ssh_command(sacct_cmd)
                
                # Parse sacct output
                if sacct_output and sacct_output.strip():
                    lines = sacct_output.strip().split('\n')
                    for line in lines:
                        if '.batch' not in line and '.extern' not in line:  # Exclude batch and external steps
                            parts = line.split('|')
                            if len(parts) >= 3:
                                state = parts[2]
                                node = parts[3] if len(parts) > 3 else ""
                                
                                # Return job status
                                return {
                                    'job_id': job_id,
                                    'status': state,
                                    'node': node
                                }
                
                # If sacct also has no information, assume the job was cancelled
                return {
                    'job_id': job_id,
                    'status': 'CANCELLED',
                    'node': None
                }
            
            # Parse squeue output
            # Format: JobName|JobId|State|NodeList|NumCPUs|Memory|TimeLimit
            parts = output.strip().split('|')
            if len(parts) >= 3:
                job_name = parts[0]
                status = parts[2]
                node = parts[3] if len(parts) > 3 and parts[3] != '(None)' else None
                cpus = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
                
                # Get and parse memory
                memory = parts[5] if len(parts) > 5 else "0"
                
                # Get time limit
                time_limit = parts[6] if len(parts) > 6 else ""
                
                # Return job status
                return {
                    'job_id': job_id,
                    'status': status,
                    'node': node,
                    'cpus': cpus,
                    'memory': memory,
                    'time_limit': time_limit
                }
            
            return None
        except Exception as e:
            logger.error(f"Failed to get job status: {e}")
            return None
    
    def cancel_job(self, job_id=None):
        """
        Cancel job
        
        Args:
            job_id: Job ID, if None use current job ID
            
        Returns:
            bool: Whether the cancellation was successful
        """
        if not job_id and self.current_job:
            job_id = self.current_job['job_id']
        
        if not job_id:
            logger.warning("Job ID not specified, unable to cancel job")
            return False
        
        try:
            cmd = f"scancel {job_id}"
            self.execute_ssh_command(cmd)
            
            # Always attempt to remove corresponding entry from local SSH config, do not rely on internal tracking state
            self._remove_ssh_config_from_local(job_id)
            
            # If in tracking set, also remove
            if job_id in self.config_written_jobs:
                self.config_written_jobs.remove(job_id)
            
            # Update current job status
            if self.current_job and self.current_job['job_id'] == job_id:
                self.current_job['status'] = 'CANCELLED'
                self.vscode_job_status_updated.emit(self.current_job)
            
            logger.info(f"Job {job_id} cancelled")
            return True
        except Exception as e:
            error_msg = f"Failed to cancel job {job_id}: {str(e)}"
            logger.error(error_msg)
            self.error_occurred.emit(error_msg)
            return False
    
    def get_running_vscode_jobs(self):
        """
        Get user's running VSCode jobs
        
        Returns:
            list: List of job information
        """
        try:
            cmd = f"squeue -u {self.username} -h -o '%j %i %T %N' | grep vscode-sshd"
            output = self.execute_ssh_command(cmd)
            
            jobs = []
            for line in output.strip().split('\n'):
                if not line.strip():
                    continue
                
                parts = line.split()
                if len(parts) >= 4:
                    job_name = parts[0]
                    job_id = parts[1]
                    status = parts[2]
                    node = parts[3]
                    
                    jobs.append({
                        'job_id': job_id,
                        'job_name': job_name,
                        'status': status,
                        'node': node
                    })
            
            return jobs
        except Exception as e:
            logger.error(f"Error getting VSCode jobs: {str(e)}")
            return []
    
    def __del__(self):
        """Destructor to ensure SSH connection is closed"""
        if hasattr(self, '_ssh_client') and self._ssh_client:
            try:
                self._ssh_client.close()
            except Exception as e:
                logging.error(f"Failed to close SSH connection: {str(e)}")
    
    def _start_poll_job_status(self, job_id):
        """
        Start a thread to poll job status
        
        Args:
            job_id: Job ID
        """
        def poll_job():
            """Thread function to poll job status"""
            try:
                # Initialize poll count
                poll_count = 0
                
                # Continuously poll until job completes or times out
                while True:
                    # Get job status
                    job_status = self.get_job_status(job_id)
                    
                    if not job_status or job_status.get('status') in ['COMPLETED', 'FAILED', 'CANCELLED', 'TIMEOUT']:
                        # Job has ended
                        logger.info(f"Job {job_id} has ended, status: {job_status.get('status') if job_status else 'UNKNOWN'}")
                        break
                    elif job_status.get('status') == 'RUNNING':
                        # Job is running, attempt to get configuration
                        if poll_count % 2 == 0:  # Check configuration every few polls
                            config = self._parse_vscode_config(job_id)
                            if config:
                                # Update job information
                                job_status['config'] = config
                                
                                # Write configuration to local SSH config (if not already written)
                                hostname = config.get('hostname')
                                if hostname and job_id not in self.config_written_jobs:
                                    # Use signal to transfer operation to main thread
                                    self._add_ssh_config_to_local(job_id, config)
                                    # Emit signal to notify configuration added
                                    self.ssh_config_added.emit(job_id, hostname)
                                    # Mark configuration as written
                                    self.config_written_jobs.add(job_id)
                                    logger.info(f"SSH configuration for job {job_id} written (first time)")
                                
                                # Emit configuration ready signal
                                self.vscode_config_ready.emit(job_status)
                    
                    # Emit status update signal
                    self.vscode_job_status_updated.emit(job_status)
                    
                    # Increment poll count
                    poll_count += 1
                    
                    # Delay
                    time.sleep(5)  # Poll every 5 seconds
                    
                    # Exit if polling exceeds a certain count
                    if poll_count > 180:  # Exit after 15 minutes
                        logger.warning(f"Polling job {job_id} status timed out")
                        break
            except Exception as e:
                logger.error(f"Failed to poll job status: {e}")
        
        # Start thread
        threading.Thread(target=poll_job, daemon=True).start()

    def _add_ssh_config_to_local(self, job_id, config):
        """
        Add SSH configuration to local ~/.ssh/config file
        
        Args:
            job_id: Job ID
            config: Configuration information
        """
        try:
            # Ensure local ~/.ssh directory exists
            ssh_dir = os.path.expanduser("~/.ssh")
            if not os.path.exists(ssh_dir):
                os.makedirs(ssh_dir, mode=0o700)
            
            # Configuration file path
            config_file = os.path.join(ssh_dir, "config")
            
            # Read existing configuration
            existing_config = ""
            if os.path.exists(config_file):
                with open(config_file, 'r') as f:
                    existing_config = f.read()
            
            # Configuration to add (with marked comments for later removal)
            hostname = config.get('hostname')
            
            # Find corresponding SSH key file path
            identity_file = self.key_path
            if not identity_file:
                # If no key path specified, try to get from user information
                from modules.auth import get_all_existing_users
                users = get_all_existing_users()
                for user in users:
                    if user['username'] == self.username:
                        identity_file = user['key_path']
                        break
            
            # If still no key path found, use default path
            if not identity_file:
                identity_file = os.path.expanduser(f"~/.ssh/{self.username}_hpc_app_key")
            
            # Construct jump host name (unique identifier)
            jump_host = f"hpc_login_{job_id}"
            
            # The compute node's host key changes per job, so we use
            # StrictHostKeyChecking=accept-new: trust-on-first-use, then pin it.
            # The old config wrote `StrictHostKeyChecking no` + `UserKnownHostsFile
            # /dev/null`, which silently accepts ANY key on EVERY connect -- i.e. no
            # protection against a man-in-the-middle on the node hostname.
            new_config = f"""
# === BEGIN HPC3 Launcher VSCode (JobID: {job_id}) ===

Host {jump_host}
    HostName {self.hostname}
    User {self.username}
    IdentityFile {identity_file}


Host {hostname}
    HostName {hostname}
    User {self.username}
    Port {config.get('port')}
    IdentityFile {identity_file}
    ProxyJump {jump_host}
    StrictHostKeyChecking accept-new
# === END HPC3 Launcher VSCode (JobID: {job_id}) ===
"""

            # Remove any block we previously wrote (new or legacy marker) first.
            existing_config = _config_block_re().sub('', existing_config)
            
            # Add new configuration to the end of the file
            with open(config_file, 'w') as f:
                if existing_config.strip():
                    f.write(existing_config.rstrip() + "\n")
                f.write(new_config)
            
            # Set correct permissions
            os.chmod(config_file, 0o600)
            
            logger.info(f"SSH configuration for job {job_id} added to {config_file}")
            
        except Exception as e:
            logger.error(f"Failed to add SSH configuration to local file: {e}")
            self.error_occurred.emit(f"Failed to add SSH configuration: {str(e)}")

    def _remove_ssh_config_from_local(self, job_id):
        """
        Remove specified job's SSH configuration from local ~/.ssh/config file
        
        Args:
            job_id: Job ID
        """
        try:
            # Configuration file path
            config_file = os.path.expanduser("~/.ssh/config")
            
            # Check if configuration file exists
            if not os.path.exists(config_file):
                logger.warning(f"SSH configuration file does not exist: {config_file}")
                return
            
            # Read existing configuration
            with open(config_file, 'r') as f:
                existing_config = f.read()
            
            # Use regex to match and remove specified job's configuration
            pattern = _config_block_re(re.escape(str(job_id)))

            # Check if matching configuration exists
            match = pattern.search(existing_config)
            if match:
                # Replace matched part with empty string
                new_config = pattern.sub('', existing_config)
                
                # Write back to file
                with open(config_file, 'w') as f:
                    f.write(new_config)
                logger.info(f"SSH configuration for job {job_id} removed from {config_file}")
                
                # Emit signal to notify configuration removed
                self.ssh_config_removed.emit(job_id)
            else:
                logger.info(f"SSH configuration for job {job_id} not found in {config_file}, no removal needed")
        except Exception as e:
            logger.error(f"Failed to remove SSH configuration from local file: {e}")
            self.error_occurred.emit(f"Failed to remove SSH configuration: {str(e)}") 