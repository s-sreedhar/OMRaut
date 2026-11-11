# OMRaut

Offline Windows desktop software for making printable answer sheets, reading scanned sheets, scoring tests, and reviewing class results. It uses one versioned JSON configuration and a local SQLite database.

## Setup (Windows)

For a ready-to-install Windows app, double-click `build.bat` in the project folder. It builds the installer and portable version under `dist\velopack`; install `OMRaut-win-Setup.exe` to launch OMRaut from the Windows Start menu. The one-time build requirements and troubleshooting steps are in [BUILD.md](BUILD.md).

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
omraut
```

When working from source, `omraut` starts the app from the activated environment. For the shortest build path, double-click `build.bat`; a .NET SDK and a supported Python (3.11–3.13) are required, and the script installs the pinned Velopack CLI if needed.

Run the labeled-scan accuracy gate with `omr-harness` (or `python -m omr_exam.harness`). It requires 100% exact bubble agreement by default and exits nonzero if there are no labeled answers. Add scans and expected answers under `tests/samples/`; see its README. Use `--allow-empty` only for a local smoke check before samples are installed. `pytest` runs the unit and synthetic reader integration tests.

## Test configuration

Each test is one versioned JSON file validated against `config/test-config.schema.json`. `examples/sample-test.json` shows the complete contract. The schema keeps sheet layout and identity fields together with exam metadata, answer keys by set, and scoring rules so the generator, reader, and scorer share one source of truth. Coordinates and dimensions are in millimetres; answer option labels are strings.

The reader corrects perspective and scan rotation from coded corner marks, classifies blank/single/faint/multiple marks, and routes ambiguous scans to operator review. The code includes synthetic generator-to-reader tests; exact accuracy on physical scans still must be demonstrated with representative labeled scan fixtures before release. No claim of universal 100% accuracy is made.

## Importing your data

- **Students and classes:** open **Students > More > Download example**, edit the CSV or JSON roster, then choose **Import roster**. Class names come from each student's `class_name`. Existing roll numbers are updated; invalid or duplicate rows stop the import. See [JSON import examples and details](docs/data-import.md).
- **Tests:** choose **JSON example** to save a valid, editable test configuration, then use **Import exam file**. Tests can include multiple subjects managed globally in Settings. The expected fields and a ready-to-edit [sample JSON](docs/examples/test-config.example.json) are documented in [the JSON guide](docs/data-import.md). Export any saved test with **Export exam file**.
- **Keep editable copies:** use **Export exam JSON** for the full test, and **Export JSON** under More on Students for the roster.

## Current desktop workflow

- **Home** shows the active test, the next step, and recent scan batches. The sidebar follows the work: Tests and Students, Scan sheets and Review, then Results.
- Create a test from a template (or import JSON), then enter its answer key by typing, pasting, Excel/JSON import, or a filled-in master sheet. Incomplete keys can be saved as drafts.
- Choose 160-question or 180-question four-column templates, or **Custom OMR** to set any printable count from 1 to the sheet capacity. A4 supports up to 180 A-D questions; print the pink, block-letter name fields at 100% scale.
- Generate a print-ready PDF with orientation-coded registration marks; print at 100% scale.
- Drag in any number of scan files and folders (images, multi-page TIFFs, PDFs), review the queue, then read them in the background with live progress and safe cancel.
- Review only what is uncertain: the scan with the reader's marks overlaid next to a keyboard-driven inspector (or an all-answers grid). Nothing unclear is guessed, and corrections are saved automatically as drafts.
- Results shows the score distribution, hardest questions and per-student detail. Exports include ranked and subject-wise mark lists, candidate responses, individual assessment charts, section/topic reports, expanded item statistics, scorecards, and Excel/CSV analysis. Source scan names, PDF page numbers, and UTC import times are retained in results exports. Exports unlock once every flagged sheet is reviewed.
- Reliability: daily automatic database copies, one-click backup/restore, recovery of interrupted batches, single-instance protection, and an error log with plain-language messages instead of crashes.
- All dialogs, menus and messages are built into the app's own design; only the operating system's file pickers are used for choosing files.

## Windows installer and updates

Install Python 3.11–3.13, .NET SDK, and the Velopack CLI (`dotnet tool install --global vpk --version 1.2.161`) on the Windows build machine, then run `installer/build.ps1` for a local package. To build, validate, version, commit, and publish a public GitHub release in one run, use `installer/publish-release.ps1`. See the [release guide](docs/release.md).

See the [operator guide](docs/user-guide.md) for the complete test, print, roster, scan, review, export, backup, and update workflow.
