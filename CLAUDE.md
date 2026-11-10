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

- **cluster_view.py** - the read-only picture of the GPU nodes and the queue
  - Free of PyQt and paramiko (except `ClusterInsights`, which holds the SSH
    session), so the parsers are unit tested against captured Slurm output
  - A paid partition and its free mirror are the *same nodes*: `sinfo` says a GPU
    is used, `squeue` says whether a paid or a free job holds it. `attribute_gpus()`
    splits each node's GPUs into paid / free / idle
  - `room()` / `fit_report()` answer "would this request start right now?" -- an
    idle GPU is often unusable because the node's cores or memory are taken
  - `free_priority()` / `best_free_account()`: a free job's queue priority is
    ~2000 x the fair-share factor of the account it is submitted under, and free
    partitions accept every account, so the account choice decides the wait
  - `avoided_nodes()`: how many *other* users have a queued job that excludes
    each node (`squeue %x`) -- the public hint that a node is faulty
  - **`ClusterInsights` only runs queries** (`sinfo`, `squeue`, `sshare`,
    `sbatch --test-only`). Nothing in this module may submit or cancel a job
  - **Account names are compared in lower case.** `sbank` prints them in capitals,
    every Slurm command in lower case; 0.5.0 matched them by exact case and so
    never found the best account. Test fixtures must use the capitals `sbank` gives

- **node_flags.py** - nodes the user flagged as bad, kept in `QSettings`
  - Every launch is kept off them (`sbatch --exclude`). A flagged name reaches a
    shell, so the store refuses anything that is not a plausible host name, even
    from a hand-edited settings file

- **hpc3_constraints.py** - which launch requests HPC3 accepts (curated, not discovered)
  - `tier_gpu_options(account, use_free)` is what makes the launch form a cascade:
    **paid** partitions require an account whose suffix matches (`…_gpu32` for
    `gpu32`), **free** partitions take any account
  - A GPU model always pins its own partition family, whatever account or family
    is passed alongside it, so A100 can never be paired with `gpu32`

- **balance.py** - Account balance and resource quota tracking
  - Queries user's compute resource allocation

- **vscode_helper.py** - VSCode remote development configuration
  - Generates SSH config entries for VSCode
  - Manages remote connection settings
  - `build_sbatch_command()` builds the one `sbatch` line, for both the real
    submission and the `--test-only` start estimate; it validates every value it
    interpolates, because a shell on the login node runs the result

- **updater.py** - Holds only the `VERSION` constant (and `GITHUB_REPO`)
  - There is no in-app updater any more; Help → Download Latest Version… opens
    the GitHub Releases page

- **ssh_config_blocks.py** - arithmetic on the launcher's own blocks in `~/.ssh/config`
  - Deliberately free of PyQt and app state, so it can be unit tested on its own
  - Its block regex backreferences the job id, so a match can never run from one
    block's BEGIN to a different block's END
  - `strip_ended_jobs()` drops blocks whose job is no longer queued; the VSCode
    page runs `VSCodeManager.sync_ssh_config()` at load and every 60 s, which
    also writes the block for any RUNNING session that lacks one
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
- **vscode_widget.py** - launch and connect to VSCode sessions (sidebar: "Sessions",
  the page the app opens on). **One thing per screen**: view 1 is the list of
  your sessions, with a detail panel for the selected one; view 2 ("New
  session") is one centred column -- the form, the free-tier advice as plain
  lines under it, and Launch pinned at the bottom. Do not put the form and the
  list back on one screen
  - The form is a cascade -- tier, account, GPU, GPU count, memory, run time
    (CPUs and bad nodes under "More options") -- and can only describe a job
    HPC3 accepts
  - The advice: the account with the best queue priority (chosen by default),
    whether the request fits an idle GPU right now, and Slurm's start estimate
    for every GPU model on demand
  - A node that several other users' queued jobs exclude is **stayed off by
    default** ("Allow it" overrides for this run). A warning alone was tried
    and a real job still landed on the broken node
- **task_manager_widget.py** - Slurm job management interface (sidebar: "Jobs")
- **node_status_widget.py** - paid / free / idle GPUs per model and per node, the
  pending queue, and your fair share per account (sidebar: "GPUs & nodes")
- **balance_widget.py** - Account balance display (sidebar: "Balance")

Look and layout live in two shared files; pages must not hard-code colours:

- **theme.py** - the palette, the Fusion style and the single stylesheet. Widgets
  opt into variants with dynamic properties (`variant="primary"`, `role="title"`,
  `card=True`)
  - Light or dark is the user's choice (sidebar switch, View > Appearance; saved
    as `ui/theme`; `HPC3_THEME=light|dark` overrides) and **changes while the app
    is running, so never cache a colour**: no `setStyleSheet(f"color: {...}")` on
    a widget. Style through a property, paint with `theme.color()` at paint time,
    or redraw on `theme.notifier.changed`
  - The look follows T3 Code's, and that is structure more than colour: content
    sits straight on the canvas; **at most one soft floating panel per column**
    (`Card()`), with further groups inside it as `Card(kind="flat")`; tables are
    lists (no box, faint header or none); controls are filled, not outlined; the
    sidebar is a tinted surface with a tinted selection and line icons
    (`theme.nav_icon`); pages have a slim top bar, not a heading and subtitle;
    sentence-case labels; a state is a small coloured dot beside plain text
    (`components.dot`). Adding a bordered box round something is the wrong fix
  - One application-wide event filter gives every button, combo box, tab and
    checkbox a pointing-hand cursor; a table whose rows do something sets the
    `clickableRows` property. Don't set cursors widget by widget
  - Windows needs its own handling, all here or in `main()`: dark mode is read
    from the registry, the title bar is darkened through DWM, the monospace face
    comes from `theme.mono_font()` (not "Menlo"), and fractional display scaling
    is passed through rather than rounded
- **components.py** - `Card`, `PageHeader`, `StatTile`, `SegmentedControl`,
  `SegmentBar`, `make_table()` and the delegates that draw pills and bars in cells

### Application Flow

1. **Startup** (`main.py:main()`):
   - Shows login dialog (always required, no auto-login)
   - User provides UCI ID, password, and DUO code
   - On successful login, creates SSH key and uploads to HPC cluster
   - Shows loading dialog with spinner animation
   - Initializes MainWindow with user credentials

2. **MainWindow** (`main.py:MainWindow`):
   - Sidebar navigation with QListWidget (the signed-in user is shown at its foot)
   - Stacked pages with QStackedWidget
   - One shared `ClusterInsights` feeds the Sessions and GPUs & nodes pages
   - Menu bar includes File→Exit and Help→Download Latest Version…/About

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
- `tests/test_exclude_nodes.py` — the VSCode panel's "Exclude Nodes" option: the
  `--exclude` list is interpolated into a shell command, so bad names must raise.
- `tests/test_cluster_view.py` — the GPU/queue snapshot, parsed from
  `tests/fixtures/cluster_snapshot.txt` (real HPC3 output with names replaced).
- `tests/test_launch_request.py` — which GPU each tier + account can reach, and the
  `sbatch` line that results (hostile values must raise).
- `tests/test_launch_form.py` — builds the real Sessions form under Qt's offscreen
  platform and clicks through it: no sequence of picks may produce an invalid
  request. It patches `QSettings` to a temp file; keep it that way.
- `tests/test_node_flags.py` — the remembered bad-node list: survives a restart,
  tolerates a corrupt settings value, refuses hostile names.
- `tests/test_theme.py` — switching light/dark at runtime (nothing may keep a
  stale colour) and pointing-hand cursors on every kind of clickable control.
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