# Sample scan fixtures

Place one scan per row and use JSON objects keyed by every configured question in the `answers` column. Optionally label `roll_number` and `set_code`; the harness checks those fields too:

```csv
image,answers,roll_number,set_code
sheet-001.png,"{""1"":""B"",""2"":null}",000123,A
```

`sample-generated-001.png` is a synthetic 300-DPI raster of the sample PDF with computer-drawn filled bubbles; it is a smoke fixture, not evidence of accuracy on real printer/scanner hardware. Add de-identified real scans from different printers, scanners, rotations, lighting conditions, faint marks, erasures, and multiple marks before treating the 100% gate as a release result. Use `null` for a blank or ambiguous expected response. Keep private student information out of committed fixtures.

Regenerate the synthetic image after changing the sheet layout with `python tools/generate_sample_fixture.py` from the repository root. It renders the current sample PDF at 300 DPI and fills the answers recorded in `truth.csv`.
