# HPC3 Launcher v0.0.2

## New Features

### Multi-GPU Support for VSCode Interactive Nodes

- **GPU Count Selection**: Added ability to request multiple GPUs (1-8) for VSCode interactive sessions
- **Dynamic UI**: GPU count spinbox automatically enables/disables based on GPU type selection
- **Enhanced Job Display**: Job information now shows the number of GPUs allocated

## Improvements

- Improved SLURM GPU resource allocation with flexible GPU count specification
- Better user experience with clear GPU configuration options
- Enhanced job metadata tracking for GPU resources

## Technical Details

- Updated `vscode_helper.py` to accept and handle `gpu_count` parameter
- Modified SLURM command generation to use `--gres=gpu:{type}:{count}` format
- Added GPU count spinbox to VSCode configuration UI

## Usage

When creating a VSCode interactive session:
1. Select a GPU account
2. Choose your GPU type (e.g., V100, A30, A100, or "Any GPU")
3. Set the number of GPUs you need (1-8)
4. Configure other resources as needed
5. Submit your job

Your interactive VSCode session will launch with access to all requested GPUs, allowing you to develop and test multi-GPU code directly on the cluster.

## System Requirements

- **Windows**: Windows 10 or later
- **macOS**: macOS 10.13 or later
- **Linux**: Ubuntu 18.04+, CentOS 7+, or similar

## Installation

Download the appropriate installer for your operating system:
- Windows: `HPC3-Launcher-0.0.2-windows.zip`
- macOS: `HPC3-Launcher-0.0.2-macos.dmg`
- Linux: `HPC3-Launcher-0.0.2-linux.deb`

## Upgrading from v0.0.1

Simply download and install the new version. Your SSH keys and configuration will be preserved.

## Known Issues

- None reported for this release

## Contributors

Special thanks to all contributors who helped with this release!
