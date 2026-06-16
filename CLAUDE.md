# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

HPC3 Launcher is a PyQt5-based desktop application that provides a graphical interface for managing UCI's HPC (High-Performance Computing) cluster resources. The application handles SSH authentication, Slurm job management, node monitoring, VSCode configuration, and account balance tracking.

## Development Environment Setup

### Using Conda (Recommended for macOS)

```bash
# Create environment from environment.yml
conda env create -f scripts/environment.yml

# Activate environment
conda activate hpc_env
```

### Using pip

```bash
pip install -r requirements.txt
```

### Running the Application

```bash
python hpc3_launcher/main.py
```

## Build Commands

### Development Build (macOS)

```bash
# One-click build script (uses conda environment hpc_env)
./build_app.sh

# This script:
# 1. Activates conda environment
# 2. Cleans old build files
# 3. Runs PyInstaller via scripts/direct_build.sh
# 4. Creates DMG installer using create_dmg.py
```

### Manual PyInstaller Build

```bash
# Activate conda environment first
conda activate hpc_env

# Run the build script
cd scripts && bash direct_build.sh && cd ..
```

### Creating DMG Installer (macOS)

```bash
python create_dmg.py
```

The output will be: `HPC3-Launcher-{VERSION}-macos.dmg`

## Architecture

### Module Structure

The application follows a modular architecture with separation between business logic (`modules/`), UI components (`ui/`), and the main application entry point.

**Core Directory Layout:**
- `hpc3_launcher/` - Main application package
  - `main.py` - Application entry point with MainWindow class
  - `modules/` - Business logic and backend functionality
  - `ui/` - PyQt5 widget components for each feature
  - `resources/` - Icons and assets

### Key Modules (`hpc3_launcher/modules/`)

- **auth.py** - SSH authentication, key management, and HPC server connectivity
  - Manages SSH key generation and upload via `ssh_key_uploader.py`
  - Handles login with password and DUO multi-factor authentication
  - Key naming convention: `{username}_hpc_app_key` (stored in `~/.ssh/`)
  - Core functions: `login_with_password()`, `check_and_login_with_key()`, `get_node_info_via_key()`

- **slurm.py** - Slurm job management via SSH commands
  - Job submission, cancellation, and status queries
  - Communicates with HPC cluster using paramiko SSH client

- **node_status.py** - Retrieves and parses cluster node information
  - Executes `sinfo` commands to get node status
  - Parses node availability, CPU/memory usage

- **balance.py** - Account balance and resource quota tracking
  - Queries user's compute resource allocation

- **vscode_helper.py** - VSCode remote development configuration
  - Generates SSH config entries for VSCode
  - Manages remote connection settings

- **updater.py** - Auto-update functionality
  - Checks GitHub releases via API: `https://api.github.com/repos/{GITHUB_REPO}/releases/latest`
  - Downloads and applies platform-specific installers (.dmg, .exe, .deb)
  - Uses `UpdateWorker` QThread for async update checks
  - Current version defined in `VERSION` constant

- **ssh_key_uploader.py** - SSH key generation and upload to HPC
  - Uses pexpect for interactive SSH sessions
  - Handles DUO authentication during key upload

### UI Components (`hpc3_launcher/ui/`)

Each widget corresponds to a page in the main application sidebar:

- **login_dialog.py** - Initial login dialog for username/password/DUO
- **task_manager_widget.py** - Slurm job management interface (sidebar: "Job Management")
- **node_status_widget.py** - Cluster node status display (sidebar: "Node Status")
- **vscode_widget.py** - VSCode configuration interface (sidebar: "VSCode Configuration")
- **balance_widget.py** - Account balance display (sidebar: "Account Balance")
- **update_dialog.py** - Update notification and download UI

### Application Flow

1. **Startup** (`main.py:main()`):
   - Shows login dialog (always required, no auto-login)
   - User provides UCI ID, password, and DUO code
   - On successful login, creates SSH key and uploads to HPC cluster
   - Shows loading dialog with spinner animation
   - Initializes MainWindow with user credentials

2. **MainWindow** (`main.py:MainWindow`):
   - Sidebar navigation with QListWidget
   - Stacked pages with QStackedWidget
   - Status bar shows logged-in username
   - Menu bar includes File→Exit and Help→Check for Updates/About
   - Auto-checks for updates 3 seconds after startup

3. **SSH Key Management**:
   - Keys are generated with fixed passphrase: `"create_key_for_hpc_app"`
   - Stored as: `~/.ssh/{username}_hpc_app_key` and `~/.ssh/{username}_hpc_app_key.pub`
   - Keys persist across sessions for password-less authentication
   - Users can delete keys via `auth.delete_user_key()`

## Build Configuration

### PyInstaller Configuration

The build process uses `scripts/direct_build.sh` which invokes PyInstaller with these key options:

```bash
python -m PyInstaller --name="HPC3-Launcher" \
    --windowed \
    --add-data="hpc3_launcher/resources:resources" \
    --add-data="hpc3_launcher/modules:modules" \
    --add-data="hpc3_launcher/ui:ui" \
    --hidden-import=pexpect \
    --hidden-import=paramiko \
    --icon="hpc3_launcher/resources/icon.ico" \
    --osx-bundle-identifier="edu.uci.hpc3launcher" \
    "hpc3_launcher/main.py"
```

**Important hidden imports**: pexpect, paramiko, cryptography, bcrypt, PyQt5 modules, requests, packaging

### Version Management

The application version is defined in `hpc3_launcher/modules/updater.py`:

```python
VERSION = "0.0.2"  # Update this for new releases
GITHUB_REPO = "Dale-Black/UCI-ClusterManager"  # Your GitHub repository
```

**To release a new version:**
1. Update `VERSION` in `hpc3_launcher/modules/updater.py` (e.g., from "0.0.2" to "0.0.3")
2. Create a release notes file: `RELEASE_NOTES_v{VERSION}.md` (e.g., `RELEASE_NOTES_v0.0.3.md`)
3. Commit all changes to git
4. Create and push a git tag: `git tag v{VERSION} && git push origin v{VERSION}`
5. GitHub Actions will automatically:
   - Build for macOS, Windows, and Linux
   - Create a GitHub release
   - Upload all installers as release assets
6. Users will be notified of the update within the application

## Dependencies

Key dependencies from `requirements.txt`:
- PyQt5==5.15.10 - GUI framework
- paramiko==3.3.1 - SSH client for HPC communication
- pexpect==4.8.0 - Interactive command automation (DUO auth)
- requests==2.31.0 - HTTP requests for update checks
- packaging==24.1 - Version comparison
- PyInstaller==6.13.0 - Application bundling

## HPC Server Configuration

The HPC server hostname is defined as a constant in `hpc3_launcher/modules/auth.py`:

```python
HPC_SERVER = 'hpc3.rcic.uci.edu'
```

## Testing

Test files are located in `tests/` directory:
- `tests/test_environment.py` - Environment and dependency checks

To run tests:
```bash
python -m pytest tests/
```

## License

The project is licensed under GPL v3.0 (changed from MIT as of commit 8a16cf5). See LICENSE file.