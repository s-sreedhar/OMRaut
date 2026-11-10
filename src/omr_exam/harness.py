"""Measure OMR reading against a complete, labeled scan set."""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from omr_exam.config import load_config

try:
    import cv2
except ImportError:  # Fixture structure checks remain useful before optional runtime packages are installed.
    cv2 = None

SAMPLE_DIR = Path(__file__).resolve().parents[2] / "tests" / "samples"
DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "examples" / "sample-test.json"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=Path, default=SAMPLE_DIR)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="test JSON used to read the samples")
    parser.add_argument("--min-accuracy", type=float, default=100.0, help="required exact bubble accuracy percentage for a non-empty labeled set")
    parser.add_argument("--allow-empty", action="store_true", help="allow a zero-sample run for a local environment smoke check")
    args = parser.parse_args()
    if not math.isfinite(args.min_accuracy) or not 0 < args.min_accuracy <= 100:
        parser.error("--min-accuracy must be a finite percentage greater than 0 and at most 100")
    sample_root = args.samples.resolve()
    truth_path = args.samples / "truth.csv"
    if not truth_path.exists():
        parser.error(f"Missing truth file: {truth_path}")
    with truth_path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
        fields = set(rows[0]) if rows else set(next(csv.reader([truth_path.read_text(encoding="utf-8-sig").splitlines()[0]])))
    missing = {"image", "answers"} - fields
    if missing:
        parser.error(f"truth.csv must contain columns: image, answers; missing {', '.join(sorted(missing))}")
    config = load_config(args.config)
    question_ids = {str(question["id"]) for question in config.get("questions", [])}
    options = {str(option) for option in config.get("sheet", {}).get("options", [])}
    set_codes = {str(code) for code in config.get("sheet", {}).get("set_codes", [])}
    errors = []
    correct = total = 0
    identity_correct = identity_total = 0
    categories: dict[str, int] = {}
    for row in rows:
        image = None
        image_path = (sample_root / row["image"]).resolve()
        try:
            image_path.relative_to(sample_root)
        except ValueError:
            errors.append(f"image path escapes sample directory: {row['image']}")
            continue
        if image_path.suffix.lower() not in IMAGE_SUFFIXES or not image_path.is_file():
            errors.append(f"missing/unsupported image: {row['image']}")
            continue
        if cv2 is not None:
            image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
            if image is None or image.size == 0:
                errors.append(f"unreadable image: {row['image']}")
        try:
            answers = json.loads(row["answers"])
            if not isinstance(answers, dict):
                raise ValueError("expected JSON object")
            if set(map(str, answers)) != question_ids:
                missing_questions = question_ids - set(map(str, answers))
                unknown_questions = set(map(str, answers)) - question_ids
                details = []
                if missing_questions:
                    details.append(f"missing questions {', '.join(sorted(missing_questions, key=int))}")
                if unknown_questions:
                    details.append(f"unknown questions {', '.join(sorted(unknown_questions))}")
                raise ValueError("truth must label every question (" + "; ".join(details) + ")")
            invalid_options = {str(answer) for answer in answers.values() if answer not in (None, "") and str(answer) not in options}
            if invalid_options:
                raise ValueError(f"unknown expected option(s): {', '.join(sorted(invalid_options))}")
            expected_roll = row.get("roll_number", "").strip()
            expected_set = row.get("set_code", "").strip()
            if expected_roll and (not expected_roll.isdigit() or len(expected_roll) != int(config["sheet"]["roll_number_digits"])):
                raise ValueError("truth roll_number does not match configured digit count")
            if expected_set and expected_set not in set_codes:
                raise ValueError(f"unknown expected set code {expected_set!r}")
            if cv2 is not None and image is not None:
                from omr_exam.reader import read_image
                result = read_image(image, config)
                for question, expected in answers.items():
                    total += 1
                    expected = None if expected is None or expected == "" else expected
                    if result.answers.get(str(question)) == expected:
                        correct += 1
                for field, expected, actual in (("roll_number", expected_roll, result.roll_number), ("set_code", expected_set, result.set_code)):
                    if expected:
                        identity_total += 1
                        if actual == expected:
                            identity_correct += 1
                for flag in result.flags:
                    categories[flag.get("kind", "unknown")] = categories.get(flag.get("kind", "unknown"), 0) + 1
        except (json.JSONDecodeError, ValueError) as exc:
            errors.append(f"{row['image']}: invalid answers JSON ({exc})")
    if errors:
        print("Fixture errors:")
        print("\n".join(f"- {item}" for item in errors))
        return 1
    check = f"{len(rows)} scan(s) decoded" if cv2 is not None else "image decode skipped (install project dependencies)"
    print(f"Fixture harness OK: {len(rows)} sample(s); {check} and truth records parsed.")
    if not rows:
        print("Add rows to tests/samples/truth.csv to measure answer-reading accuracy.")
        return 0 if args.allow_empty else 2
    elif cv2 is not None:
        rate = 100 * correct / total if total else 0
        print(f"Bubble accuracy: {correct}/{total} = {rate:.2f}% (all labeled bubble positions; no target can be claimed when denominator is zero)")
        if identity_total:
            identity_rate = 100 * identity_correct / identity_total
            print(f"Identity accuracy: {identity_correct}/{identity_total} = {identity_rate:.2f}% (labeled roll/set fields)")
        else:
            identity_rate = 100.0
        print(f"Reader flags: {json.dumps(categories, sort_keys=True)}")
        if not total:
            print("Accuracy gate not met: no non-blank labeled answers were found.")
            return 3
        if rate < args.min_accuracy or identity_rate < args.min_accuracy:
            print(f"Accuracy gate failed: bubble and labeled identity fields each need at least {args.min_accuracy:.2f}%.")
            return 3
    else:
        print("Accuracy gate not run: install OpenCV and NumPy to read the scans.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
