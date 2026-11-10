import csv
import json

import pytest

from omr_exam import harness


def test_accuracy_gate_rejects_non_finite_or_out_of_range_threshold(monkeypatch):
    monkeypatch.setattr("sys.argv", ["omr-harness", "--min-accuracy", "nan"])
    with pytest.raises(SystemExit) as error:
        harness.main()
    assert error.value.code == 2


def test_truth_image_path_cannot_escape_sample_directory(tmp_path, monkeypatch, capsys):
    samples = tmp_path / "samples"
    samples.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"not a scan")
    truth = samples / "truth.csv"
    with truth.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["image", "answers"])
        writer.writeheader()
        writer.writerow({"image": "../outside.png", "answers": json.dumps({str(i): "A" for i in range(1, 6)})})
    config = harness.DEFAULT_CONFIG
    monkeypatch.setattr("sys.argv", ["omr-harness", "--samples", str(samples), "--config", str(config)])

    assert harness.main() == 1
    assert "image path escapes sample directory" in capsys.readouterr().out
