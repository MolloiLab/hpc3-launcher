# Quick Release Guide - v0.0.2

You've already pushed your code changes. Now you need to create a release. Here's what to do:

## Option 1: Command Line (Recommended)

Since you already pushed your commits, just create and push the tag:

```bash
# Create the tag for v0.0.2
git tag v0.0.2

# Push the tag to GitHub
git push origin v0.0.2
```

**That's it!** GitHub Actions will automatically:
1. Build the installers for all platforms (takes ~10-15 minutes)
2. Create the release
3. Upload all the files

## Option 2: GitHub Web Interface

If you prefer using the GitHub website:

1. Go to: https://github.com/Dale-Black/UCI-ClusterManager/releases
2. Click **"Draft a new release"**
3. In "Choose a tag", type: `v0.0.2` and click "Create new tag: v0.0.2 on publish"
4. Set "Release title" to: `HPC3 Launcher v0.0.2`
5. Copy and paste the contents from `RELEASE_NOTES_v0.0.2.md` into the description
6. Click **"Publish release"**

This will trigger the same automated build process.

## What Happens Next

1. **GitHub Actions Starts**: Check the "Actions" tab in your repo
2. **Builds Run**: You'll see three parallel builds (macOS, Windows, Linux)
3. **Release Created**: After ~10-15 minutes, check the "Releases" page
4. **Files Attached**: The release will have:
   - `HPC3-Launcher-0.0.2-macos.dmg`
   - `HPC3-Launcher-0.0.2-windows.zip`
   - `HPC3-Launcher-0.0.2-linux.deb`

## Monitoring the Build

Watch the build progress:
1. Go to: https://github.com/Dale-Black/UCI-ClusterManager/actions
2. Click on the workflow run for `v0.0.2`
3. You can see each platform building in real-time

## If Something Goes Wrong

### Build Fails
- Click into the failed job to see the error
- Fix the issue in your code
- Delete the tag: `git tag -d v0.0.2 && git push origin :refs/tags/v0.0.2`
- Make your fixes, commit, and try again

### Need to Update After Release
- Bump the version to `0.0.3`
- Make your changes
- Create a new release with `v0.0.3`

## Verifying Success

Once complete, verify:
1. ✅ Release appears at: https://github.com/Dale-Black/UCI-ClusterManager/releases
2. ✅ All three installer files are attached
3. ✅ Release notes are displayed correctly
4. ✅ You can download and test the installers

## Next Time

For future releases, just remember:
```bash
# 1. Update version in updater.py
# 2. Create release notes file
# 3. Commit and push
git add .
git commit -m "Release v0.0.X: Description"
git push

# 4. Create and push tag
git tag v0.0.X
git push origin v0.0.X
```

That's it! GitHub Actions handles everything else automatically.
