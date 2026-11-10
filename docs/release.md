# Windows release and Velopack updates

## Build a testable Windows package

Use Windows x64 with Python 3.11, 3.12, or 3.13, the .NET SDK, and the matching Velopack CLI:

```powershell
dotnet tool install --global vpk --version 1.2.161
.\installer\build.ps1
```

If the supported Python interpreter is not the default `python` on PATH, set `OMR_PYTHON` to its full executable path before running the script.

If the build machine cannot reach PyPI, run the **Windows desktop validation** workflow with `workflow_dispatch` on GitHub. Its Windows runner installs the dependencies, builds the package, and attaches the Velopack output as a downloadable workflow artifact.

The build creates `dist/OMRExamApp/OMRExamApp.exe`, runs its packaged smoke check, then writes Velopack artifacts to `dist/velopack`. Velopack 1.2.161 names the installer `OMRExamApp-win-Setup.exe` and the portable package `OMRExamApp-win-Portable.zip`. It also creates a full `.nupkg`, `releases.win.json`, `assets.win.json`, and `RELEASES`; a delta `.nupkg` is created when an earlier full package remains in the output directory. Install `OMRExamApp-win-Setup.exe` to test the installed app and its update lifecycle. Keep the full app directory for direct app-folder testing. User data lives in the Windows application-data directory and is retained across upgrades.

The build smoke check validates bundled resources, SQLite initialization, sheet PDF generation and ingestion, marked-answer reader accuracy, dashboard loading and navigation from saved SQLite records, and opening the desktop window with Qt's offscreen platform. Windows CI additionally installs the Velopack package in a temporary directory, runs the installed smoke check, builds a local N+1 release, serves the feed on localhost, and verifies that the installed app restarts at the expected version. These checks do not replace manual scan and operator workflow checks.

## Configure OTA updates

The app uses Velopack's Python `App` and `UpdateManager`. Set `OMR_UPDATE_SOURCE_URL` to the HTTPS base URL where the `dist/velopack` release feed and packages will be hosted, then build. The build embeds this feed URL in the application; an empty value disables OTA checks for local builds. Velopack checks package integrity against hashes in the release feed, and HTTPS protects the feed and downloads in transit. This build does not add a separate signed-feed verification key or Authenticode signing, so restrict publishing access and treat code signing as a required pre-release distribution step.

For each release, increment `omr_exam.__version__`, build with the same `OMR_UPDATE_SOURCE_URL`, and upload every file emitted in `dist/velopack` to the same hosted directory while preserving filenames. Publish the release feed last so clients cannot discover packages that are not yet uploaded. Use HTTPS and restrict who can publish release files. Do not overwrite an already published version; publish a higher semantic version.

For a local update test, build version N with the local HTTP server URL embedded, install it from `OMRExamApp-win-Setup.exe`, then build version N+1 to the same `dist/velopack` directory without deleting version N's full package. Serve the release files over localhost HTTP, use the app's **Check for updates** action, and verify download and restart from the installed package. Keep the N+1 `releases.win.json` in the served feed and preserve the referenced packages. Python integration has no test locator, so verify updates through an actual installed package.

## Validation before distribution

1. Run `python -m pytest -q`, including the synthetic PDF-to-reader integration tests.
2. Add representative labeled physical scans to `tests/samples/` and run `omr-harness`. A zero-sample run is not an accuracy pass; ambiguous marks must remain in human review.
3. Build, install, scan, review, export, back up, and restore on a clean Windows VM.
4. Install a staging release and verify an N-to-N+1 update and restart before promoting the feed.

Passing synthetic or finite labeled samples does not prove universal 100% accuracy. Keep ambiguous scans in human review.
