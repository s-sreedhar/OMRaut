# Add students, classes, and tests with JSON

OMRaut stores student and test data locally. Importing a roster adds or updates students by `roll_number`; it does not remove students missing from the file. If a roll number already exists, the imported name and class replace that roster entry. The import is validated before any rows are written.

## Student roster and classes

Choose **Students → Import roster → JSON**. A class is the `class_name` value on each student. Once imported, class names appear as suggestions in the student and new-test forms. You can also type a new class directly into those fields.

```json
{
  "students": [
    { "roll_number": "000123", "name": "Asha Rao", "class_name": "10A" },
    { "roll_number": "000124", "name": "Ravi Shah", "class_name": "10A" },
    { "roll_number": "000205", "name": "Mina Das", "class_name": "10B" }
  ]
}
```

Keep roll numbers as JSON strings so leading zeroes are preserved. `class_name` may be omitted or empty. Duplicate roll numbers in one import are rejected before changes are applied.

## Tests

Create a test in **Tests → New test**, or use **Import exam file** to import a complete exam JSON file. To create a reusable file, select a test and choose **Export exam file**. The exported file includes the sheet layout, question list, scoring rules, set codes, and answer keys. Importing it creates a separate test record; review the title, class, date, and answer keys before printing sheets.

The class and subject fields suggest values used by earlier tests and the student roster; both still accept new values. Set codes are managed with **Add set** and **Remove**. Removing a set requires confirmation, and the final remaining set cannot be removed. In single-paper mode, set bubbles are omitted from the printed sheet.

## Safety checks

- Use unique roll numbers and quote them as strings.
- Keep a backup of the exported JSON before making bulk edits.
- Inspect imported test details and every answer key before printing or scanning.
- JSON imports do not transmit roster or exam data anywhere.
