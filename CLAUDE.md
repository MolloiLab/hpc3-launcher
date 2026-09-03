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
conda activate hpc-mgmt
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
# One-click build script (uses conda environment hpc-mgmt)
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
conda activate hpc-mgmt

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
  - `core/` - SSH plumbing shared by everything else (see below)
  - `modules/` - Business logic and backend functionality
  - `ui/` - PyQt5 widget components for each feature
  - `resources/` - Icons and assets
- `formal/` - TLA+ model of the concurrent `~/.ssh/config` edit; `formal/check.sh`

### `hpc3_launcher/core/`

- **ssh_session.py** - all SSH plumbing, and the source of truth for `HPC_SERVER`,
  `SSH_DIR` and the `_hpc_app_key` naming convention (`modules/auth.py` re-exports them)
  - `HPCSession` - a pooled, key-based paramiko connection
  - `SSHWorker` - a QThread every slow operation runs inside. **Nothing that touches
    the network may run on the Qt UI thread**; doing so freezes the window.
  - `SSHError` carries an actionable `.hint` for the UI

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

- **ssh_config_blocks.py** - arithmetic on the launcher's own blocks in `~/.ssh/config`
  - Deliberately free of PyQt and app state, so it can be unit tested on its own
  - Its block regex backreferences the job id, so a match can never run from one
    block's BEGIN to a different block's END
  - `strip_blocks_for_node()` drops blocks for a node whose job has ended;
    `prune_broken_blocks()` drops blocks OpenSSH would refuse to parse
  - **Anything written here can break every SSH host the user has**: OpenSSH abandons
    the whole file on one bad directive, so never interpolate an unvalidated value

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
   - Keys are **ed25519 and passphraseless** — that is what makes later logins
     non-interactive. (An older version used RSA-4096 with a hardcoded passphrase.)
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

**Do not edit the version by hand, and do not create tags by hand.** release-please
owns both. The version lives in `hpc3_launcher/modules/updater.py` on a line marked
`# x-release-please-version`, mirrored in `.release-please-manifest.json`; editing
either by hand desynchronises them.

```python
VERSION = "X.Y.Z"  # x-release-please-version   <- release-please rewrites this line
GITHUB_REPO = "MolloiLab/hpc3-launcher"  # update checks + releases
```

**To release:** write [Conventional Commits](https://www.conventionalcommits.org/)
(`feat:`, `fix:`, …) and merge them to `main`. release-please keeps a "Release PR"
open that bumps the version and updates `CHANGELOG.md`. Merging that PR tags
`vX.Y.Z`, publishes the release, and dispatches `release.yml`, which builds the
macOS `.dmg`, Windows `setup.exe` and Linux `.deb`, launch-tests each published
installer on its own OS, and appends the install guide to the release notes.

There are no hand-written `RELEASE_NOTES_*.md` files; `CHANGELOG.md` is generated.

## Dependencies

Key dependencies from `requirements.txt`:
- PyQt5==5.15.10 - GUI framework
- paramiko==3.3.1 - SSH client for HPC communication
- pexpect==4.8.0 - Interactive command automation (DUO auth)
- requests==2.31.0 - HTTP requests for update checks
- packaging==24.1 - Version comparison
- PyInstaller==6.13.0 - Application bundling

## HPC Server Configuration

The HPC server hostname is defined as a constant in `hpc3_launcher/core/ssh_session.py`
(and re-exported by `modules/auth.py`):

```python
HPC_SERVER = 'hpc3.rcic.uci.edu'
```

## Testing

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

- `tests/test_ssh_config.py` — the `~/.ssh/config` writer: Slurm value cleaning,
  job-state normalisation, block pruning, and end-to-end writes. Several tests shell
  out to the **real `ssh -G`** rather than trusting our own idea of valid syntax.
- `tests/test_ssh_config_blocks.py` — block arithmetic in `modules/ssh_config_blocks.py`.
- `tests/test_environment.py` — a dependency-check script, not a unittest module.

CI runs this suite on **ubuntu, macOS and Windows**. The Windows job matters: the
config this app writes is parsed by Windows' own OpenSSH, and bugs have shipped that
only manifest there. `HPC3_REQUIRE_SSH=1` makes a missing `ssh` a hard failure rather
than a silent skip.

`main.py` also supports a headless smoke test used by CI:

```bash
HPC3_SMOKE_TEST=1 HPC3_SMOKE_MARKER=/tmp/marker.txt QT_QPA_PLATFORM=offscreen \
  python hpc3_launcher/main.py
```

## License

The project is licensed under GPL v3.0 (changed from MIT as of commit 8a16cf5). See LICENSE file.