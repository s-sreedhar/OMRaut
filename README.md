# OMR Exam App

Offline Windows desktop software for making printable answer sheets, reading scanned sheets, scoring tests, and reviewing class results. It uses one versioned JSON configuration and a local SQLite database.

## Setup (Windows)

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
omr-exam
```

Run the labeled-scan accuracy gate with `omr-harness` (or `python -m omr_exam.harness`). It requires 100% exact bubble agreement by default and exits nonzero if there are no labeled answers. Add scans and expected answers under `tests/samples/`; see its README. Use `--allow-empty` only for a local smoke check before samples are installed. `pytest` runs the unit and synthetic reader integration tests.

## Test configuration

Each test is one versioned JSON file validated against `config/test-config.schema.json`. `examples/sample-test.json` shows the complete contract. The schema keeps sheet layout and identity fields together with exam metadata, answer keys by set, and scoring rules so the generator, reader, and scorer share one source of truth. Coordinates and dimensions are in millimetres; answer option labels are strings.

The reader corrects perspective and scan rotation from coded corner marks, classifies blank/single/faint/multiple marks, and routes ambiguous scans to operator review. The code includes synthetic generator-to-reader tests; exact accuracy on physical scans still must be demonstrated with representative labeled scan fixtures before release. No claim of universal 100% accuracy is made.

## Current desktop workflow

- Dashboard lists local tests and recent batches; double-click a recent batch to reopen its review queue.
- Build a test, question sections, sets, roll-number length, options, optional printed name/class fields, and scoring rules in the app. Maintain each set's key manually, by paste, Excel import, or by reading a completed master sheet.
- Generate a print-ready PDF with orientation-coded registration marks. Print at 100% scale.
- Upload individual image files, multi-page TIFFs, or scan PDFs, or select a folder of scans. Blank answers are reported, while faint, multiple, rotated/unreadable, invalid-ID, and duplicate-ID cases are routed to review.
- Inspect the aligned sheet and zoom into each answer row, correct values, and re-score. Official exports are blocked while unresolved review-required sheets remain.
- Import a student roster from CSV or Excel (`roll_number,name,class_name`; `Roll No`, `Student Name`, and `Class` headings are also accepted) and export results to CSV/XLSX/PDF. The Excel workbook includes result, item, topic, and subject analysis tabs; the PDF report adds analysis tables after the class marklist; analyses can also be exported individually as CSV. Excel numeric cells formatted with leading zeroes preserve those roll numbers. Re-keying a test re-scores saved responses without rescanning.
- Use the in-app database backup/restore controls to protect local records.
- Configure PDF render DPI (150–600), grayscale/color ingestion, and default correct/incorrect/blank marking values in Settings. These preferences are stored in the local SQLite database; each result records its color mode and PDF render DPI when applicable.

## Windows installer and updates

Install Python 3.11–3.13, .NET SDK, and the Velopack CLI (`dotnet tool install --global vpk --version 1.2.161`) on the Windows build machine, then run `installer/build.ps1`. If the supported interpreter is not the default `python`, set `OMR_PYTHON` to its executable path. The script produces a smoke-checked PyInstaller app folder and Velopack setup/update artifacts under `dist/velopack`. Set `OMR_UPDATE_SOURCE_URL` to the HTTPS feed URL before building to enable in-app OTA updates; leave it unset for a local test build. See [release notes](docs/release.md).

See the [operator guide](docs/user-guide.md) for the complete test, print, roster, scan, review, export, backup, and update workflow.
