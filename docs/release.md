# Windows release and Velopack updates

## Build a testable Windows package

Use Windows x64 with Python 3.11, 3.12, or 3.13, the .NET SDK, and the matching Velopack CLI:

```powershell
dotnet tool install --global vpk --version 1.2.161
.\installer\build.ps1
```

If the supported Python interpreter is not the default `python` on PATH, set `OMR_PYTHON` to its full executable path before running the script.

If the build machine cannot reach PyPI, run the **Windows desktop validation** workflow with `workflow_dispatch` on GitHub. Its Windows runner installs the dependencies, builds the package, and attaches the Velopack output as a downloadable workflow artifact.

The build creates `dist/OMRaut/OMRaut.exe`, runs its packaged smoke check, then writes Velopack artifacts to `dist/velopack`. Velopack 1.2.161 names the installer `OMRaut-win-Setup.exe` and the portable package `OMRaut-win-Portable.zip`. It also creates a full `.nupkg`, `releases.win.json`, `assets.win.json`, and `RELEASES`; a delta `.nupkg` is created when an earlier full package remains in the output directory. Install `OMRaut-win-Setup.exe` to test the installed app and its update lifecycle. Keep the full app directory for direct app-folder testing. User data lives in the Windows application-data directory and is retained across upgrades.

The build smoke check validates bundled resources, SQLite initialization, sheet PDF generation and ingestion, marked-answer reader accuracy, dashboard loading and navigation from saved SQLite records, and opening the desktop window with Qt's offscreen platform. Windows CI additionally installs the Velopack package in a temporary directory, runs the installed smoke check, builds a local N+1 release, serves the feed on localhost, and verifies that the installed app restarts at the expected version. These checks do not replace manual scan and operator workflow checks.

## Configure OTA updates

The app uses Velopack's Python `App` and `UpdateManager`. For this public GitHub repository, set `OMR_UPDATE_SOURCE_URL` to `https://github.com/s-sreedhar/OMRaut` before building. Velopack detects GitHub Releases as its update source. Configured builds check once per day after startup and keep the manual **Check for updates** action in Settings. A detected release shows a persistent in-app notice; users open Settings, click **Update available**, then confirm before download/restart. No update and background network errors are silent. An empty URL disables automatic update checks. Velopack verifies packages against the release feed's hashes; this build does not add Authenticode signing, so Windows may show a SmartScreen warning until a trusted signing certificate is configured.

Attach each release's `releases.win.json`, `assets.win.json`, full `.nupkg`, installer, and portable zip to the GitHub Release. Keep the files from one build together and preserve their filenames. Do not publish a partial feed or overwrite a published version; publish a higher semantic version. Public OTA requires a public repository. A private GitHub source requires a token, and embedding a personal access token in a desktop app is not appropriate.

The existing prebuilt 0.1.0 executable has an empty update source. The build command below rebuilds the same version with the public GitHub source before publishing it. If 0.1.0 has already been published, do not replace it: publish a higher version, and note that users of the old OTA-disabled build must install the new version manually once.

## Publish a public GitHub release

The current repository is private. A GitHub Release inherits repository visibility, so anonymous downloads require changing the repository to public; otherwise invite each recipient as a collaborator. GitHub CLI requires explicit acceptance of the effects when changing visibility.

After reviewing `git status`, commit and push the intended source changes. Do not stage the generated executables from `.venv-build`:

```powershell
gh auth login
gh auth status
gh repo view s-sreedhar/OMRaut --json visibility,url

# Only if you intend anyone to be able to browse and download the project:
gh repo edit s-sreedhar/OMRaut --visibility public --accept-visibility-change-consequences

git add -A
git reset -- .venv-build/Scripts/omr-exam.exe .venv-build/Scripts/omr-harness.exe
git diff --cached --check
git status --short
git commit -m "Prepare OMRaut 0.1.0 release"
git push origin main
```

Build a clean release directory with OTA enabled. Use a new output directory for the first release; the default `dist/velopack` currently contains earlier OMRExamApp artifacts. Do not reuse an output directory that already has the same version.

```powershell
$env:OMR_PYTHON = (Resolve-Path .\.venv-build\Scripts\python.exe).Path
$env:OMR_UPDATE_SOURCE_URL = "https://github.com/s-sreedhar/OMRaut"
$env:OMR_VELOPACK_OUTPUT_DIR = (Join-Path (Get-Location) "dist\velopack-public-ota-final")
$env:OMR_TEST_TEMP_ROOT = (Join-Path (Get-Location) "tmp\update-test")
$env:TEMP = (Join-Path (Get-Location) "tmp\update-test-temp")
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
.\installer\build.ps1
.\installer\test-installed-package.ps1
```

Once the build and installed-package check pass, create and push the tag, then attach the complete OMRaut release feed plus the user-facing installer and portable zip. Check first that the tag is unused; if it exists, stop and use a newly incremented app version instead.

```powershell
git ls-remote --tags origin refs/tags/v0.1.0
# Continue only if the command printed no tag.
git tag -a v0.1.0 -m "OMRaut 0.1.0"
git push origin v0.1.0

$feed = $env:OMR_VELOPACK_OUTPUT_DIR
gh release create v0.1.0 `
  "$feed\OMRaut-win-Setup.exe" `
  "$feed\OMRaut-win-Portable.zip" `
  "$feed\OMRaut-0.1.0-full.nupkg" `
  "$feed\releases.win.json" `
  "$feed\assets.win.json" `
  "$feed\RELEASES" `
  --repo s-sreedhar/OMRaut --verify-tag --title "OMRaut 0.1.0" `
  --notes "Windows installer, portable app, and OTA update feed for OMRaut 0.1.0."

gh release view v0.1.0 --repo s-sreedhar/OMRaut
```

Share `https://github.com/s-sreedhar/OMRaut/releases/latest`. Verify it in a signed-out browser or another account; a private repo or draft release is not publicly downloadable. GitHub CLI documents that `gh release create` attaches the supplied assets to the tag's release and publishes it.

For every later public OTA release, increment the version in both `src/omr_exam/__init__.py` and `pyproject.toml`, then build to the same OTA output directory so Velopack can make a delta. Keep the existing full packages in that directory. Run the installed-package test before publishing. Push a new `vMAJOR.MINOR.PATCH` tag and attach the new version's full and delta `.nupkg` files plus the new installer, portable ZIP, `releases.win.json`, `assets.win.json`, and `RELEASES`. A GitHub release should contain only one full package and one delta package; GitHub's Velopack source searches earlier releases for older package versions. Never replace a published version or its feed. Public OTA needs a public repository; the app must not contain a personal access token.

Example future release after changing both version fields to `0.1.1`:

```powershell
$version = "0.1.1"
$tag = "v$version"
$feed = $env:OMR_VELOPACK_OUTPUT_DIR
$packages = @(Get-ChildItem -LiteralPath $feed -Filter "OMRaut-$version-*.nupkg" | ForEach-Object FullName)
if ($packages.Count -lt 1) { throw "No package for $version in $feed" }
git ls-remote --tags origin "refs/tags/$tag"
# Continue only when the command printed no tag.
git tag -a $tag -m "OMRaut $version"
git push origin $tag
gh release create $tag `
  "$feed\OMRaut-win-Setup.exe" `
  "$feed\OMRaut-win-Portable.zip" `
  $packages `
  "$feed\releases.win.json" `
  "$feed\assets.win.json" `
  "$feed\RELEASES" `
  --repo s-sreedhar/OMRaut --verify-tag --title "OMRaut $version" `
  --notes "Windows installer, portable app, and OTA update feed for OMRaut $version."
```

## Validation before distribution

1. Run `python -m pytest -q`, including the synthetic PDF-to-reader integration tests.
2. Add representative labeled physical scans to `tests/samples/` and run `omr-harness`. A zero-sample run is not an accuracy pass; ambiguous marks must remain in human review.
3. Build, install, scan, review, export, back up, and restore on a clean Windows VM.
4. Install a staging release and verify an N-to-N+1 update and restart before promoting the feed.

Passing synthetic or finite labeled samples does not prove universal 100% accuracy. Keep ambiguous scans in human review.
