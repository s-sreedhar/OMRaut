# OMR Exam App - Operator Guide

This guide covers the offline desktop workflow. Tests, rosters, scans, results, and settings are stored in the current Windows user's application-data folder. Backup bundles are written to the location chosen by the operator.

The **Dashboard** lists saved tests and the ten most recent scan batches. Double-click a recent batch to open its review queue.

## 1. Start the app and create a test

1. Open **OMR Exam App** from the Start menu or desktop shortcut.
2. In **Tests and sheets**, choose **New test**.
3. Enter the test title, class, subject, date, sections, question count, answer options, roll-number length, test-set codes, and question columns. Choose whether the printed sheet includes name and class writing fields; these fields are not read automatically.
4. Check the correct, incorrect, and blank points. These start from the defaults saved under **Settings**. Add bonus, dropped, or partial-credit rules when needed.
5. Save the test. The answer-key editor opens. Select a set and enter one answer per question. The key must cover all questions before sheets can be generated or scans processed.

You can also import a JSON test configuration. Save answer keys by set in the **Answer keys** editor using manual entry, pasted question/answer rows, an Excel workbook, or a completed master sheet. Excel keys can use `Question`/`Question Number`/`Q` and `Answer`/`Answer Key`/`Key`/`Option` columns, or list exactly one answer per question in order. A master-sheet import must have readable, single answer marks and the selected set must match the bubbled set; the student roll number may be left blank.

## 2. Make and print answer sheets

1. Select the test in **Tests and sheets**.
2. Choose **Make sheet PDF** and save the PDF.
3. Print at 100% scale. Keep all four corner marks on the page; do not crop or resize to fit.
4. Photocopy or print one copy per student. Students should fill one option per question and one bubble per roll-number digit and test set.

Name and class are printed as writing fields for operator identification. They are not read automatically; the roll number is the roster key.

## 3. Import the student roster

In **Results and reports**, choose **Import students CSV/Excel**. The file needs roll number and name columns; class is optional. Accepted headings include `roll_number`, `name`, `class_name`, `Roll No`, `Student Name`, and `Class`. Excel numeric roll-number cells preserve leading zeroes when formatted with a zero-padded number format such as `000000`.

When a roster is present, sheets whose roll number is absent from it are flagged for review. Duplicate roll numbers within one batch are also flagged.

## 4. Scan a batch

1. In **Settings**, choose PDF rendering resolution (150–600 DPI) and grayscale or color input mode. The default marking scheme also applies to newly created tests. Choose **Save settings** to persist changes.
2. Use a scanner setting of at least 150 DPI and avoid cropping, heavy skew, shadows, or low contrast.
3. In **Scan batch**, choose **Upload scanned images** to select files, or **Select scan folder** to load supported files directly inside one folder. Supported scans include image files, multi-page TIFFs, and scan PDFs.
4. Follow the progress dialog. Cancelled batches, pages that fail to load, and interrupted batches remain marked incomplete and cannot produce official exports. A completed batch should have the expected number of pages and no file/page errors.

Use **View batch log** on the Scan batch tab to reopen the selected batch's saved summary, reader review flags, and per-file/page errors. The log is stored with the batch in SQLite and remains available after restarting the app.

The reader corrects sheet rotation and perspective from coded corner marks. It records answers, roll number, set, per-sheet thresholds, PDF render DPI when applicable, and the input color mode.

## 5. Review uncertain sheets

Open **Review queue** after scanning. Select a row or question to view its aligned sheet or answer crop. Double-click roll number, set, or answer cells to correct them. The **Flags** column explains issues such as faint, multiple, unreadable, invalid ID, unknown student, or duplicate ID.

Blank answers are shown as informational flags; they do not require correction unless the operator intends to change them. Every sheet requiring review must be inspected and have **Reviewed** checked before saving. **Save corrections and re-score** validates the entire batch and commits all corrections and audit entries together. If validation fails, no row in that save batch is committed.

## 6. Export results and analysis

In **Results and reports**, select the active batch and choose a report:

- Class marklist: CSV, Excel, or PDF
- Student scorecards: PDF
- Item difficulty/discrimination: CSV
- Topic or subject point summaries: CSV

The Excel marklist workbook contains separate **Results**, **Item analysis**, **Topic analysis**, and **Subject analysis** tabs.
The PDF report contains the class marklist followed by item, topic, and subject analysis tables.

Exports are blocked until the batch is complete and all review-required sheets are resolved. Updating an answer key in the editor saves the new key and re-scores stored responses without rescanning.

## 7. Back up data and update the app

Use **Backup data** to save the SQLite database and managed scans/previews as a backup bundle. **Restore backup** replaces the active app data after creating a safety backup. Preferences such as scan DPI, color mode, and default marking scheme are stored in SQLite and included in backups.

Use **Check for updates** to check the configured Velopack feed. When an update is available, approve the download and restart to let Velopack apply it. Builds without an update feed configured report that updates are not configured.

## Troubleshooting

| Flag or message | What to check |
| --- | --- |
| Could not find corner marks / unreadable | Confirm the complete sheet is visible, all marks are present, and the scan is at least 150 DPI. |
| Faint mark | Ask the student to darken the bubble or verify the intended answer in the preview. |
| Multiple marks | Confirm the intended option in the preview and correct the answer before saving. |
| Invalid or unknown roll number | Compare with the printed sheet and imported roster; correct the roll number and recheck duplicate IDs. |
| Test key incomplete | Enter an answer for every question in every set before scanning. |
| Questions do not fit on the page | Use fewer questions per column, more columns (up to four), or a larger sheet size in the imported JSON configuration. |
| Batch incomplete | Re-select failed files/pages and run a clean batch; partial or cancelled batches cannot be exported as official results. |

## Accuracy note

The bundled labeled image is computer-generated. Its exact match and deterministic image-degradation tests verify the code path, but they do not measure accuracy on physical paper, printers, scanners, erasures, or student marking styles. Before operational use, add de-identified labeled scans representative of the target scanners and marking conditions, then run `omr-harness` and review the denominator and flags.
