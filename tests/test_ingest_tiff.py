from pathlib import Path

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from omr_exam.ingest import count_scan_pages, scan_pages


def test_multipage_tiff_ingestion_reads_each_frame(tmp_path: Path):
    if not hasattr(cv2, "imwritemulti"):
        pytest.skip("This OpenCV build cannot write multi-page TIFF fixtures")
    path = tmp_path / "scanner-output.tiff"
    expected = [np.full((32, 48, 3), intensity, dtype=np.uint8) for intensity in (40, 120, 220)]
    assert cv2.imwritemulti(str(path), expected)
    assert count_scan_pages(path) == 3
    pages = list(scan_pages(path))
    assert [label.rsplit("#page=", 1)[-1] for label, _image in pages] == ["1", "2", "3"]
    assert [int(np.median(image)) for _label, image in pages] == [40, 120, 220]
    grayscale = list(scan_pages(path, color_mode="grayscale"))
    color = list(scan_pages(path, color_mode="color"))
    assert grayscale[0][1].ndim == 2
    assert color[0][1].ndim == 3


def test_broken_tiff_is_rejected_during_page_count(tmp_path: Path):
    path = tmp_path / "broken.tif"
    path.write_bytes(b"not a TIFF")
    with pytest.raises(ValueError, match="Could not decode TIFF"):
        count_scan_pages(path)


def test_scan_ingestion_rejects_unsupported_dpi_and_color_mode(tmp_path: Path):
    path = tmp_path / "one.png"
    assert cv2.imwrite(str(path), np.full((32, 48), 255, dtype=np.uint8))
    with pytest.raises(ValueError, match="DPI must be between 150 and 600"):
        list(scan_pages(path, dpi=100))
    with pytest.raises(ValueError, match="Color mode must be"):
        list(scan_pages(path, color_mode="indexed"))
