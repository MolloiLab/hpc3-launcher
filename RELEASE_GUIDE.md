# Release Guide for HPC3 Launcher

This guide will walk you through releasing a new version of HPC3 Launcher.

## Quick Release Checklist

- [ ] Update version number in `hpc3_launcher/modules/updater.py`
- [ ] Create release notes file `RELEASE_NOTES_v{VERSION}.md`
- [ ] Commit all changes
- [ ] Create and push git tag
- [ ] Verify GitHub Actions builds successfully
- [ ] Test the release installers

## Detailed Steps

### 1. Update Version Number

Edit `hpc3_launcher/modules/updater.py` and update the `VERSION` constant:

```python
VERSION = "0.0.3"  # Change to your new version
```

### 2. Create Release Notes

Create a file named `RELEASE_NOTES_v{VERSION}.md` (e.g., `RELEASE_NOTES_v0.0.3.md`) with the following structure:

```markdown
# HPC3 Launcher v0.0.3

## New Features
- List your new features here

## Improvements
- List improvements here

## Bug Fixes
- List bug fixes here

## Known Issues
- List any known issues

## Installation
Download the appropriate installer for your operating system:
- Windows: `HPC3-Launcher-0.0.3-windows.zip`
- macOS: `HPC3-Launcher-0.0.3-macos.dmg`
- Linux: `HPC3-Launcher-0.0.3-linux.deb`
```

### 3. Commit Your Changes

```bash
git add .
git commit -m "Release v0.0.3: Add multi-GPU support"
```

### 4. Create and Push Git Tag

```bash
# Create the tag
git tag v0.0.3

# Push the tag to GitHub
git push origin v0.0.3

# Also push your commits
git push origin main
```

**Important**: The tag MUST start with `v` (e.g., `v0.0.3`) to trigger the GitHub Actions workflow.

### 5. Monitor GitHub Actions Build

1. Go to your GitHub repository
2. Click on the "Actions" tab
3. You should see a workflow run for your tag
4. Wait for all three builds (macOS, Windows, Linux) to complete (usually takes 10-15 minutes)

### 6. Verify the Release

Once the workflow completes:

1. Go to the "Releases" section of your GitHub repository
2. You should see a new release named "HPC3 Launcher v0.0.3"
3. Verify that all three installers are attached:
   - `HPC3-Launcher-0.0.3-macos.dmg`
   - `HPC3-Launcher-0.0.3-windows.zip`
   - `HPC3-Launcher-0.0.3-linux.deb`

### 7. Test the Release (Recommended)

Download the installer for your platform and test:
- Installation works correctly
- Application launches successfully
- New features work as expected
- Auto-update feature detects the new version

## Troubleshooting

### Build Fails on GitHub Actions

1. Check the Actions log for error messages
2. Common issues:
   - Syntax errors in Python code
   - Missing dependencies in `requirements.txt`
   - Build script errors

### Release Notes Not Showing Up

- Make sure your release notes file is named exactly: `RELEASE_NOTES_v{VERSION}.md`
- The version must match your git tag (e.g., tag `v0.0.3` needs `RELEASE_NOTES_v0.0.3.md`)

### Auto-Update Not Working

- Verify `GITHUB_REPO` in `updater.py` matches your repository name
- Check that the release is not marked as "draft" or "prerelease"
- Ensure the version number format is correct (e.g., "0.0.3", not "v0.0.3")

## Version Numbering Convention

Use semantic versioning: `MAJOR.MINOR.PATCH`

- **MAJOR**: Incompatible API changes or major rewrites
- **MINOR**: New features in a backwards-compatible manner
- **PATCH**: Backwards-compatible bug fixes

Examples:
- `0.0.1` → `0.0.2`: Bug fixes or small improvements
- `0.0.2` → `0.1.0`: New features added
- `0.1.0` → `1.0.0`: Major release, stable API

## Rolling Back a Release

If you need to delete a release:

```bash
# Delete the tag locally
git tag -d v0.0.3

# Delete the tag on GitHub
git push origin :refs/tags/v0.0.3
```

Then manually delete the release from GitHub's Releases page.

## Additional Notes

- The GitHub Actions workflow builds for all three platforms automatically
- Users with existing installations will be notified of updates within 24 hours (or sooner if they check manually)
- The auto-update feature downloads the appropriate installer for each platform
- macOS users get `.dmg` files, Windows users get `.zip` files, Linux users get `.deb` packages
