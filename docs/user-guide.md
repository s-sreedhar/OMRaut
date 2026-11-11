# OMRaut - Operator Guide

OMRaut works fully offline. Tests, rosters, scans, results and settings are stored in your Windows user's application-data folder (`%APPDATA%\OMRaut`; installations that predate the rename keep using their existing `%APPDATA%\OMR Exam App` folder, so nothing is lost or duplicated).

The sidebar follows the work in order: **Home**, then **Prepare** (Tests, Students), **Process** (Scan sheets, Review) and **Report** (Results). The **Home** page shows the active test, the next step to take, and recent scan batches. The **Active test** selector at the top applies to every page.

## 1. Create a test and answer key

1. Choose **New test** (top right, or **Tests**). Pick the template closest to your exam, name it, and optionally open **Customize questions, sets, and marking**.
2. The answer-key editor opens. Type the whole key into **Type the whole key** (`ABCDABCD...` or `1-A 2-B ...`) and choose **Fill this set** (use the Set A / Set B switch to pick the set), or click a cell and type the letter: it is saved at once and the cursor moves down by itself. There are no drop-down lists to open. Other options: paste from a spreadsheet, import Excel or JSON, or read a filled-in master sheet. **Download a sample file to fill in** saves an example key (JSON, Excel or one-line text) for this test that you can edit and import back.
3. Every question needs an answer in every set before sheets can be printed or scanned. If you are not ready, choose **Save as draft** and finish later; Home keeps reminding you.

## 2. Print answer sheets

Put your institute's name and logo on the sheets in **Settings > Institute** (both optional; the logo is a PNG or JPG, printed in black and white at the top right). A test can carry its own institute name from **New test > Customize**; the exam name is the test title. These sit in the header and never affect reading.

In **Tests**, select the test and choose **Make sheet PDF**. Choose the 160- or 180-question template, or **Custom OMR** and set the count and columns to fit your exam. A4 supports up to 180 A-D questions in four columns. Print at **100% scale** and keep all four corner squares on the page. The name and class fields have block-letter guides for people; the roll number bubbles are what the reader uses.

## 3. Add students (optional but recommended)

In **Students**, choose **Import roster** (Excel or CSV with `roll_number`, `name`, `class_name`; headings such as `Roll No` and `Student Name` also work) or **Add student**. With a roster present, sheets whose roll number is unknown or duplicated are sent to Review.

## 4. Scan sheets

1. Open **Scan sheets**. Drag in any number of files and folders at once, or use **Add files** (select many) / **Add a folder**. Images, multi-page TIFFs and PDFs are accepted. Files are listed first so you can remove any before starting; nothing is read until you choose **Start reading**.
2. Reading runs in the background with live progress; you can cancel safely. Everything read so far is kept.
3. When it finishes you see how many sheets were read and how many need review. **View batch log** shows per-file notes and survives restarts.

Use a scanner setting of at least 150 DPI; a flat, well-lit photo also works. Reading quality for PDFs (DPI) and colour mode are in **Settings**.

## 5. Review uncertain sheets

**Review** lists only the sheets that need a decision. For each one you see the scan with the reader's marks drawn on it, and the question decisions below the image (close-up of the bubbles included). Scroll vertically through the sheet, drag to pan while zoomed, and use Ctrl+wheel or the zoom buttons to zoom:

- Press **A-E** to choose an answer, **Space** for blank, **Up/Down** to move between marks, **Ctrl+Enter** to confirm the sheet and jump to the next.
- Fix a wrong roll number or set at the top of the panel; unknown students can be added to the roster in one click.
- Prefer a spreadsheet view? Switch to **All answers grid** and edit cells directly, then **Save all corrections**.

Nothing unclear is ever guessed. Your corrections are **saved automatically as drafts** the moment you make them, so closing the window, a crash or a power cut will not lose them; the sheet shows a *Draft* tag until you confirm it.

## 6. Results and exports

**Results** shows the headline figures (average, median, highest, lowest, pass rate), the score distribution with the standard deviation and the test's reliability (KR-20), a **Question analysis** table (hardest first, with discrimination and the most chosen wrong option), a ranked student table (score, percent, percentile) and every answer for the selected student.

Choose **Export results** to open the export window. Save any single file with **Save as...**, or tick several and choose **Export selected to a folder** to write them all in one step. Available reports include ranked and subject-wise mark lists, one response analysis and assessment chart per student, per-student section/topic performance, expanded item statistics, scorecards, an Excel workbook with analysis tabs, and CSV marklist/item/distractor/topic/subject files. Results exports retain the original uploaded scan filename, PDF page number, and import timestamp in UTC. Exports stay locked until every flagged sheet has been reviewed and the batch is complete. Re-saving an answer key re-scores stored sheets without rescanning.

## 7. Stability, backups and updates

- **Automatic safety copy** of your data once per day (last 7 are kept) in the data folder's `auto-backups`.
- **Back up now / Restore from backup** in **Settings**. Restoring first makes a safety copy of what you have.
- Settings save the moment you change them.
- If a scan batch is interrupted (crash, power loss), it is closed as incomplete on the next start; scored sheets are kept and the log explains what happened.
- Only one OMRaut window can run at a time, so two copies never write to the same data. Unexpected errors show a plain message instead of closing the app, and details go to `logs/omr-exam.log` in the data folder.
- **Check for updates** checks the configured Velopack feed. Configured installations also check quietly once per day after startup; when a release is available, a banner tells you to open Settings and click **Update available**. You confirm before download and restart. Builds without an update source report that updates are not configured when you check manually.

## Troubleshooting

| Flag or message | What to check |
| --- | --- |
| Corner marks not found / unreadable | The whole sheet and all four corner squares must be visible; scan at 150 DPI or higher. |
| Faint mark | Ask the student to fill bubbles fully, or confirm the intended answer in Review. |
| More than one mark | Choose the intended answer in Review. |
| Roll number unclear / not in roster | Compare with the printed sheet and fix the roll number, or add the student. |
| Answer key incomplete | Every question in every set needs an answer before printing or scanning. |
| Exports locked | Review every flagged sheet first; the Results banner has a **Go to Review** shortcut. |
| Batch incomplete | Add the failed files again in a new batch; partial or cancelled batches cannot be exported. |

## Accuracy note

The bundled labeled image is computer-generated; it verifies the code path but not accuracy on physical paper, printers, scanners, erasures or marking styles. Before operational use, add de-identified labeled scans from your own scanners, run `omr-harness`, and review the results.
