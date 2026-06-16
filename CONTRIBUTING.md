# Contributing to HPC3 Launcher

Thank you for your interest in contributing to HPC3 Launcher! This document provides guidelines and instructions for contributing to this project.

## Code of Conduct

By participating in this project, you agree to maintain a respectful and inclusive environment for everyone.

## How to Contribute

There are many ways to contribute to the project:

1. **Reporting bugs**: Create an issue with a clear description of the bug, steps to reproduce, and if possible, screenshots.
2. **Suggesting features**: Create an issue with a detailed description of your proposed feature.
3. **Submitting code changes**: Fork the repository, make your changes, and submit a pull request.

## Development Setup

### Prerequisites

- Python 3.8+
- PyQt5
- Git

### Local Development Environment

1. Clone the repository:
   ```bash
   git clone https://github.com/songliangyu/UCI-ClusterManager.git
   cd UCI-ClusterManager
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Run the application:
   ```bash
   python hpc3_launcher/main.py
   ```

## Pull Request Process

1. **Fork the repository**.
2. **Create a branch** with a descriptive name for your change.
3. **Make your changes**, following the coding style of the project.
4. **Test your changes** thoroughly.
5. **Submit a pull request** with a clear description of the changes.

## Coding Standards

- Follow PEP 8 style guidelines.
- Write docstrings for all classes and functions.
- Include comments where necessary.
- Write tests for new functionality.

## Building and Testing

To build the application:
```bash
python pyinstaller_build.py
```

To create installers:
```bash
python create_installer.py
```

## License

By contributing to HPC3 Launcher, you agree that your contributions will be licensed under the project's GNU General Public License v3.0 (GPL-3.0).