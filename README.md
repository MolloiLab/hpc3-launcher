# HPC3 Launcher

A desktop app for working with UCI's **HPC3** cluster without living in the terminal:

- **Job Management** — view, submit, and cancel Slurm jobs
- **Node Status** — live CPU / memory / GPU usage across nodes
- **VSCode** — launch one or **multiple** remote VSCode sessions (each writes its own `~/.ssh/config` entry, so `Remote-SSH: Connect to Host…` just works)
- **Account Balance** — your SU balance per account

Sign in once with your UCInetID + Duo; the app installs an SSH key so every later launch is password-less.

> Renamed from `UCI-ClusterManager`. Originally by Song Liangyu and contributors; maintained by the Molloi Lab. GPL-3.0.

## Download

Grab the latest installer from the [**Releases**](https://github.com/MolloiLab/hpc3-launcher/releases) page:

| Platform | File | Admin rights |
|----------|------|--------------|
| macOS 11+ (Apple Silicon or Intel) | `HPC3-Launcher-<version>-macos.dmg` | not needed |
| Windows 10/11 (64-bit) | `HPC3-Launcher-<version>-windows-setup.exe` | not needed |
| Debian / Ubuntu (64-bit) | `HPC3-Launcher-<version>-linux.deb` | needed, for `dpkg` |

Nothing else is required — Python, Qt and the SSH tooling are bundled. Every release page
carries the same install guide you'll find below, written for the exact build attached to it
(it knows whether that build was signed).

## Before you start

- Your **UCInetID** and password, and **Duo** on your phone.
- **Off campus? Connect to the UCI VPN first.** HPC3 isn't reachable from the open internet, so
  without it the app reports that it can't reach the cluster.

## Installing on macOS (first launch)

The macOS build is **not yet notarized by Apple**, so the first launch needs one extra step. This is a one-time thing.

1. Open the `.dmg` and drag **HPC3-Launcher** into **Applications**. No admin rights? Drag it into `~/Applications` in your home folder instead (create that folder if it doesn't exist) — it runs the same.
2. Clear the download quarantine flag (this is what lets it open without the *"Apple cannot check it for malicious software"* error). Open **Terminal** and run (adjust the path if you used `~/Applications`):

   ```bash
   xattr -dr com.apple.quarantine "/Applications/HPC3-Launcher.app"
   ```

3. Open **HPC3-Launcher** from Applications normally.

<details>
<summary>Prefer not to use Terminal?</summary>

Double-click the app; when macOS blocks it, go to **System Settings → Privacy & Security**, scroll to the message about HPC3-Launcher, and click **Open Anyway**. Then open the app again and choose **Open**.
</details>

<details>
<summary>It says HPC3-Launcher "is damaged and can't be opened"</summary>

Nothing is damaged — that's macOS's generic wording for an app without a paid Developer ID signature, and on Apple Silicon clearing quarantine alone is sometimes not enough. Re-sign it locally and it opens:

```bash
codesign --force --deep --sign - "/Applications/HPC3-Launcher.app"
```
</details>

> Once the project has an Apple Developer ID configured (see `docs/SIGNING.md`), releases are notarized automatically and this step goes away — users just double-click.

## Installing on Windows (first launch)

The Windows build installs **per-user and needs no admin rights** — it works on lab machines where you don't have administrator access.

1. Download and double-click `HPC3-Launcher-<version>-windows-setup.exe`. It installs to your user profile (no UAC/admin prompt) and adds a Start Menu shortcut.
2. The installer (and first app launch) is **not yet code-signed**, so Windows **SmartScreen** may show *"Windows protected your PC."* Click **More info → Run anyway**. This is a one-time thing.

HPC3 Launcher uses Windows' built-in **OpenSSH Client**, enabled by default on Windows 10 version 1809 and later. If signing in fails with `ssh-keygen not found`, turn it on once under **Settings → Apps → Optional features → Add a feature → OpenSSH Client**.

> Once Azure Trusted Signing is configured (see `docs/SIGNING.md`), releases are signed automatically and this warning goes away.

## Installing on Linux

```bash
sudo dpkg -i HPC3-Launcher-<version>-linux.deb
sudo apt-get install -f   # only if dpkg reports missing dependencies
```

Then launch **HPC3 Launcher** from your applications menu, or run `/usr/local/bin/HPC3-Launcher`.

## First launch

Sign in with your UCInetID and password, then approve the **Duo Push** on your phone. The app
generates an SSH key (`~/.ssh/<UCInetID>_hpc_app_key`) and installs it on HPC3, so every launch
after this one connects with no password and no Duo prompt.

Uninstalling leaves that key and the app's `~/.ssh/config` entries in place. To clear them too,
delete `~/.ssh/<UCInetID>_hpc_app_key*` and any `HPC3 Launcher VSCode` blocks in `~/.ssh/config`.

## Running from source

```bash
# Option A: conda (matches CI)
conda env create -f scripts/environment.yml   # creates env "hpc-mgmt"
conda activate hpc-mgmt

# Option B: venv
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python hpc3_launcher/main.py
```

## Releases & CI

Releases are automated:

- **release-please** watches `main` and keeps a "Release PR" open that bumps the version and updates `CHANGELOG.md` from [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, …).
- Merging that PR tags `vX.Y.Z` and publishes a GitHub Release.
- The **build-release** workflow then builds the macOS `.dmg`, Windows `setup.exe` (Inno Setup installer), and Linux `.deb` and attaches them to that release.
- **Every release is launch-tested on all three OSes before it's trusted.** A `verify-<os>` job re-downloads the *published* installer, installs/extracts it exactly as a user would, and launches the frozen app headlessly (`QT_QPA_PLATFORM=offscreen`). The app builds its full main window, confirms the Qt event loop starts, and writes a marker; the job fails if it doesn't — so a package that can't actually run (the old Windows failure mode) turns the release red instead of shipping broken.

- Once all three verify jobs pass, a **release install guide** is appended to the release body — download table, per-platform first-launch steps, troubleshooting. It adapts to how that build was signed (no `xattr` instructions on a notarized DMG), and re-running the workflow updates the guide in place rather than appending a second copy.

To set up zero-friction (notarized) macOS builds, see [`docs/SIGNING.md`](docs/SIGNING.md).

## License

GNU GPL v3.0 — see [LICENSE](LICENSE).
