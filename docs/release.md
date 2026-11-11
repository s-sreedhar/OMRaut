# Windows release and Velopack updates

## One-command public release

From the repository root, run:

```powershell
.\installer\publish-release.ps1
```

The script checks GitHub CLI login, public repository visibility, the `main` branch, and remote synchronization. It finds the next unused patch version by checking both GitHub tags and the existing Velopack feed, updates the app version in `src/omr_exam/__init__.py` and `pyproject.toml`, then:

1. Builds the packaged app and Velopack setup, portable ZIP, and OTA feed.
2. Places temporary files on the project drive to avoid filling the Windows system temp drive.
3. Installs the package in a temporary directory and checks the local update path.
4. Stages the current source changes, excludes the generated `.venv-build` launchers, and displays the exact files to be committed.
5. Waits for you to type `PUBLISH` before it pushes source and tag and creates the public GitHub Release with all current-version packages and feed files.

For this checkout, where `0.1.0` is already in the feed and published, the next version will be `0.1.1`. If the build fails before producing a package, fix the reported issue and rerun the same command. If the package was built but the installed-package check failed, rerunning reuses that package and repeats the check. Do not delete earlier full packages from the feed directory because Velopack uses them to produce update deltas.

The script uses `dist/velopack-public-ota-final` as its persistent feed directory and sets `OMR_UPDATE_SOURCE_URL` to `https://github.com/s-sreedhar/OMRaut`. The repository is public, which allows anonymous downloads and the app's public OTA feed. The release includes the installer, portable ZIP, current-version `.nupkg` files, `releases.win.json`, `assets.win.json`, and `RELEASES`.

After success, verify the uploaded assets with:

```powershell
gh release view <tag-shown-by-script> --repo s-sreedhar/OMRaut
```

Share `https://github.com/s-sreedhar/OMRaut/releases/latest`. Never replace a published version; the script skips versions already present in GitHub or the local feed.

## Build prerequisites

Use Windows x64 with Python 3.11, 3.12, or 3.13, the .NET SDK, Git, authenticated GitHub CLI, and Velopack CLI 1.2.161:

```powershell
dotnet tool install --global vpk --version 1.2.161
gh auth login
```

The script uses `.venv-build\Scripts\python.exe` when available, otherwise Python on `PATH`. Set `$env:OMR_PYTHON` before running it if a different supported interpreter is required. The app's automatic OTA checks are silent when no update is available; when one is available, users see a notice and choose when to install it. The application does not include a GitHub personal access token.

## Validation limits

The build and installed-package check cover bundled resources, startup, basic application behavior, and the local Velopack update path. They do not establish scan accuracy on real-world sheets. Before wider distribution, verify representative labeled physical scans, printing, scanning, review, export, backup, and restore on a clean Windows machine. Ambiguous scans must remain in operator review.
