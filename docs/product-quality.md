# Product benchmark and quality plan

Research checked Addmen's public product pages on 10 November 2026. Those pages describe a workflow spanning sheet design, scan alignment, mark reading, exception correction, test/set management, scoring, analysis, exports, and training/support. They advertise arbitrary standard scanners, skew/rotation correction, blank and multiple-mark alerts, answer-key changes with re-scoring, and configurable marking. The pages do not publish an independently reproducible accuracy benchmark or disclose their recognition algorithms, so those claims are a feature benchmark, not proof of a particular implementation or accuracy level.

Sources: [Addmen OMR software features](https://www.addmengroup.com/OMR/omr-software-features.htm), [Addmen OMR workflow and results](https://www.admengroup.com/omr-software.htm), [Addmen user guide](https://www.addmengroup.com/omr/addmen-omr-user-guide.htm).

## Product workflow implemented in this project

1. Create a test and a configurable print-ready sheet from the same versioned JSON configuration.
2. Capture a batch of scans, normalize orientation and perspective from registration marks, and read marks against calibrated per-sheet thresholds.
3. Route ambiguous bubbles, unreadable IDs, duplicate students, and incomplete scan batches to review before publishing scores.
4. Score each set from its answer key and marking rules. Re-evaluate saved responses when the key or scoring changes; never require rescanning.
5. Produce student, class, topic, and item-analysis views, then export reproducible CSV, XLSX, and PDF reports.
6. Keep an offline SQLite database with backups, restore, and an audit trail for corrections and re-scoring.

## Accuracy and release gates

- Keep labeled scans from multiple printers, scanners, DPI values, rotations, faint marks, erasures, and multi-marks. Split calibration and held-out evaluation samples so thresholds are not tuned against the scorecard.
- The fixture harness now gates a labeled scan set at 100% exact agreement by default and reports the measured denominator. Passing a finite test set cannot prove universal 100% accuracy; uncertain reads are held for review and every official CSV, XLSX, PDF, item-analysis, and topic-analysis export independently rejects unresolved sheets.
- Never silently turn uncertain reads into confident answers. Threshold margins and image quality should produce review flags, with the original crop and operator correction retained.
- Compare scoring totals and report aggregates with hand-calculated cases, including negative/decimal/partial/bonus/dropped scoring and all test sets.
- The core suite now stores and scores a 50-sheet batch across all three sample sets, checks per-student hand totals, and verifies the exported CSV grand total (835 points for the deterministic fixture).
- Verify recovery from interrupted batch processing and backup restore before installer release. Incomplete/cancelled batches remain marked in SQLite and cannot produce official exports, even after restart. Test the packaged build on a clean Windows machine.

## Current implementation state and limits

The desktop code includes a dashboard for saved tests and recent batches, a test builder, multi-set answer-key editor/import, PDF sheet generator, image/TIFF/PDF batch ingestion, coded-corner perspective/orientation correction, mark flags, aligned scan preview, answer correction/audit, re-scoring, student CSV/XLSX import, class exports, item/topic/subject analysis, SQLite backup/restore, and Velopack update integration. The PyInstaller one-folder app is configured to be packaged by Velopack. Scan batches persist expected and processed page counts, errors, and completion state; cancelled, interrupted, or failed batches stay blocked from official exports. Synthetic reader tests exercise the generator-to-reader path, including rotated sheets and double marks.

The fixture folder contains one synthetic raster with five marked answers; the current fixture harness decodes it and passes the labeled gate (5/5 bubbles and 2/2 identity fields). A deterministic 50-sheet synthetic batch also passed reader, scoring, and SQLite integration checks. Additional synthetic stress cases cover brightness/contrast adjustment, JPEG quality 72, moderate sensor noise, 150-DPI downscaling, and one-degree rotation. These cases derive from computer-generated pages; no real labeled scans are available, so accuracy on physical scanners, paper, and mark styles remains unmeasured. The local environment lacks a supported Python runtime with the GUI dependencies, so a packaged GUI, Velopack installer, and installed update cycle have not yet been produced or verified. OTA release builds require an HTTPS feed URL. Handwriting recognition, QR/barcodes, duplex templates, a full drag-and-drop designer, and bulk background processing remain outside the implementation.
