# Import students and tests

OMRaut keeps roster and test data on this computer. JSON import validates the complete file before writing any records.

## Student roster JSON

Choose **Students → Import roster → JSON**. Each student needs a roll number and name; `class_name` is optional. A class is a reusable label on a student and is offered as a suggestion when creating tests. Imports add or update matching roll numbers; they do not delete students missing from the file.

```json
{
  "students": [
    { "roll_number": "000123", "name": "Asha Rao", "class_name": "10A" },
    { "roll_number": "000124", "name": "Ravi Shah", "class_name": "10A" }
  ]
}
```

Keep roll numbers as strings to preserve leading zeroes. Duplicate roll numbers within one file are rejected before changes are applied.

## Test JSON

On **Tests**, choose **JSON example** to save a complete, valid test configuration, or start from [`test-config.example.json`](examples/test-config.example.json). Edit the values and use **Import exam file**. The app validates the whole configuration and sheet layout before adding the test. Import creates a new test record; it does not overwrite the selected test.

The root object must contain these fields:

| Field | Purpose |
| --- | --- |
| `schema_version` | Currently `1`. |
| `test` | `title`, ISO date (`YYYY-MM-DD`), `class`, and a `subjects` array with one or more names; optional `sections` and `institute`. |
| `sheet` | Printable page dimensions, question/column counts, `options`, `set_codes`, roll digit count, and printed fields. |
| `questions` | One ordered entry per question, with consecutive `id`, `section`, and optional `topic`. |
| `sets` | One object per set code. Each has an `answer_key` mapping question IDs to valid option codes. |
| `scoring` | Points for correct, incorrect, and blank responses; optional bonus, dropped, and partial-credit rules. |
| `reader` | Optional mark and faint-mark thresholds. |

The starter file is the recommended input shape. Subjects are selected from the global **Settings → Classes and subjects** lists. Questions are assigned to subjects through `test.sections`; each section has a subject and a question range. Keep `sheet.set_codes` and the names under `sets` identical, make each code unique, and use every question ID from `1` through the question count. Answer keys may be empty while preparing a test, but must be completed before scanning results.

Add or edit reusable class and subject names in **Settings → Classes and subjects**. Students and tests use the same class list; each test can select several subjects. Renaming a global name updates saved records. Removing a name hides it from future choices while preserving existing records.

Set codes are plain text. In the new-test form, type each code into its own field, use **Add set** or **Remove**, and keep at least one. Codes must be nonblank and unique without regard to case. A single-paper test has one set internally, but its printed sheet omits the set label and set bubbles.

## Before printing

- Keep roll numbers quoted as strings.
- Check test title, class, selected subjects, date, options, and all answer keys.
- Print at 100% / actual size on the configured paper size; disable fit-to-page scaling.
- Keep an exported JSON backup before making bulk edits.
- Imports stay local and are not transmitted by OMRaut.
