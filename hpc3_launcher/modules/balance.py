#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import logging
import paramiko
import threading
from PyQt5.QtCore import QObject, pyqtSignal

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BalanceManager(QObject):
    """
    Computing resource balance manager for querying user's resource usage and balance
    """
    
    # Signal definitions
    balance_updated = pyqtSignal(dict)  # Balance information update signal
    error_occurred = pyqtSignal(str)    # Error signal
    
    def __init__(self, hostname, username, key_path=None, password=None):
        """
        Initialize balance manager
        
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
        # self-reacquire deadlocks the worker thread (the old code hid this by
        # connecting eagerly in __init__, so the inner connect_ssh was never hit
        # while the lock was held; lazy connect exposed it).
        self.lock = threading.RLock()
        
        # Cache SSH client
        self._ssh_client = None
        
        # Data cache
        self.data_cache = {
            'last_refresh': 0,
            'balance_data': None
        }
        # Connect lazily (see NodeStatusManager) -- connecting in __init__ blocks
        # the UI thread that constructs us, freezing the window right after login.
    
    def connect_ssh(self):
        """Connect to SSH server"""
        try:
            with self.lock:
                if self._ssh_client and self._ssh_client.get_transport() and self._ssh_client.get_transport().is_active():
                    logger.debug(f"[BalanceManager] Reusing existing SSH connection to {self.hostname} for balance operations")
                    return True
                
                # Create new SSH client
                logger.info(f"[BalanceManager] Establishing new SSH connection to {self.hostname} for balance operations")
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
                
                logger.info(f"[BalanceManager] SSH connection to {self.hostname} established successfully")
                return True
        except Exception as e:
            logger.error(f"[BalanceManager] SSH connection to {self.hostname} failed: {e}")
            self.error_occurred.emit(f"SSH connection failed: {str(e)}")
            return False
    
    def _close_ssh_client(self):
        """Safely close SSH client connection"""
        with self.lock:
            if self._ssh_client:
                try:
                    logger.info(f"[BalanceManager] Closing SSH connection to {self.hostname}")
                    self._ssh_client.close()
                except Exception as e:
                    logger.error(f"[BalanceManager] Error closing SSH connection: {e}")
                    pass
                self._ssh_client = None
    
    def execute_ssh_command(self, command):
        """
        Execute SSH command and return results
        
        Args:
            command: Command to execute
            
        Returns:
            str: Command output
        """
        with self.lock:
            if not self._ssh_client or not self._ssh_client.get_transport() or not self._ssh_client.get_transport().is_active():
                logger.info(f"[BalanceManager] SSH connection not active, reconnecting to {self.hostname}")
                if not self.connect_ssh():
                    raise Exception("Unable to connect to SSH server")
            
            try:
                logger.debug(f"[BalanceManager] Executing command on {self.hostname}: {command}")
                stdin, stdout, stderr = self._ssh_client.exec_command(command, timeout=30)
                output = stdout.read().decode('utf-8')
                error = stderr.read().decode('utf-8')
                
                if error and not output:
                    logger.error(f"[BalanceManager] Command error on {self.hostname}: {error}")
                    raise Exception(f"Command execution error: {error}")
                
                return output
            except Exception as e:
                logger.error(f"[BalanceManager] Command execution failed on {self.hostname}: {e}")
                # Try to reconnect
                self.connect_ssh()
                raise
    
    def get_user_balance(self, username=None, reporter=None):
        """
        Get user's resource balance information

        Args:
            username: Username to query, defaults to current logged-in user
            reporter: optional SSHWorker reporter for live status (may be None)

        Returns:
            dict: Dictionary containing user balance information
        """
        username = username or self.username

        try:
            if reporter:
                reporter.state("loading", f"reading SU balance for {username}")
            # Execute sbank command to get balance
            cmd = f"sbank balance statement -u {username}"
            output = self.execute_ssh_command(cmd)
            
            # Parse output
            balance_data = self._parse_balance_output(output, username)
            
            # Send signal to notify UI update
            self.balance_updated.emit(balance_data)
            
            # Update cache
            self.data_cache['balance_data'] = balance_data
            
            return balance_data
        except Exception as e:
            error_msg = f"Failed to get user balance: {str(e)}"
            logger.error(error_msg)
            self.error_occurred.emit(error_msg)
            return None
    
    def _parse_balance_output(self, output, target_username):
        """
        Parse sbank command output
        
        Args:
            output: sbank command output string
            target_username: Target username
            
        Returns:
            dict: Parsed balance data
        """
        result = {
            'username': target_username,
            'accounts': [],
            'total_available': 0,
            'total_usage': 0
        }
        
        lines = output.strip().split('\n')
        
        # Need at least 3 lines (including two header lines and at least one data line)
        if len(lines) < 3:
            return result
        
        # Skip header lines
        current_account = None
        
        for line in lines[2:]:  # Skip first two header lines
            # Skip empty lines
            if not line.strip():
                continue
                
            # Match line data
            parts = re.split(r'\s+\|\s+', line.strip())
            
            if len(parts) != 3:
                continue
            
            # Parse each part
            user_part = parts[0].strip()
            account_part = parts[1].strip()
            limit_part = parts[2].strip()
            
            # Parse user part
            user_parts = re.split(r'\s+', user_part)
            if len(user_parts) < 2:
                continue
                
            username = user_parts[0]
            
            # Check if contains asterisk (marking current user)
            is_current = '*' in user_part
            user_usage = int(user_parts[-1].replace(',', ''))
            
            # Parse account part
            account_parts = re.split(r'\s+', account_part)
            if len(account_parts) < 2:
                continue
                
            account_name = account_parts[0]
            account_usage = int(account_parts[-1].replace(',', ''))
            
            # Parse limit and available parts
            limit_parts = re.split(r'\s+', limit_part)
            if len(limit_parts) < 2:
                continue
                
            account_limit = int(limit_parts[0].replace(',', ''))
            available = int(limit_parts[-1].replace(',', ''))
            
            # If username matches target user, add to results
            if username == target_username:
                # Check if this is a new entry for the same account
                if current_account and current_account['name'] == account_name:
                    # Update existing account information
                    current_account['user_usage'] += user_usage
                else:
                    # Create new account entry
                    current_account = {
                        'name': account_name,
                        'user_usage': user_usage,
                        'account_usage': account_usage,
                        'account_limit': account_limit,
                        'available': available,
                        'is_personal': account_name.upper() == target_username.upper()
                    }
                    result['accounts'].append(current_account)
                
                # Accumulate total usage
                if is_current:
                    result['total_usage'] += user_usage
                    
                    # If it's a personal account, add to total available
                    if current_account['is_personal']:
                        result['total_available'] += available
        
        return result
    
    def refresh_balance(self):
        """
        Force refresh balance information
        
        Returns:
            dict: Updated balance information
        """
        return self.get_user_balance()
    
    def __del__(self):
        """Destructor function to ensure SSH connection is closed"""
        if hasattr(self, '_ssh_client') and self._ssh_client:
            try:
                self._ssh_client.close()
            except Exception as e:
                logging.error(f"Failed to close SSH connection: {str(e)}") 