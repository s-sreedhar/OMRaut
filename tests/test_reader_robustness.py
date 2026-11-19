"""Trust tests: the reader must read good marks exactly and flag every doubtful one,
at any orientation, tilt, resolution, lighting, or photocopy quality."""
import pytest

cv2 = pytest.importorskip("cv2")
fitz = pytest.importorskip("fitz")
import numpy as np

from omr_exam.layout import create_layout
from omr_exam.reader import read_image
from omr_exam.sheets import generate_sheet_pdf
from omr_exam.templates import build_from_template, get_template

ANSWERS = {str(q): "ABCD"[(q * 7) % 4] for q in range(1, 181)}
ROLL = "2026001"


@pytest.fixture(scope="module")
def sheet(tmp_path_factory):
    config = build_from_template(get_template("large180"), title="Robust QA", class_name="QA", subject="Science")
    config["sheet"]["roll_number_digits"] = 7
    config["sheet"]["set_codes"] = ["A"]
    config["sets"] = {"A": config["sets"][next(iter(config["sets"]))]}
    folder = tmp_path_factory.mktemp("robust")
    pdf = generate_sheet_pdf(config, folder / "blank.pdf")
    document = fitz.open(pdf)
    pix = document[0].get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72), alpha=False)
    blank = cv2.cvtColor(np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n),
                         cv2.COLOR_RGB2GRAY)
    document.close()
    return config, blank


class Marker:
    def __init__(self, config, blank):
        self.config = config
        self.image = blank.copy()
        self.layout = create_layout(config)
        self.sx = self.image.shape[1] / self.layout.width
        self.sy = self.image.shape[0] / self.layout.height

    def bubble(self, group, index, value):
        return next(b for b in self.layout.bubbles
                    if b.group == group and b.index == str(index) and b.value == str(value))

    def centre(self, group, index, value):
        b = self.bubble(group, index, value)
        return round(b.x * self.sx), round(b.y * self.sy)

    def fill(self, group, index, value, radius_mm=None, colour=30):
        radius_mm = radius_mm or self.layout.bubble_radius * 0.92
        cv2.circle(self.image, self.centre(group, index, value), round(radius_mm * self.sx), colour, -1, cv2.LINE_AA)

    def cross(self, group, index, value):
        x, y = self.centre(group, index, value)
        r = round(self.layout.bubble_radius * 0.9 * self.sx)
        t = max(2, round(0.3 * self.sx))
        cv2.line(self.image, (x - r, y - r), (x + r, y + r), 30, t, cv2.LINE_AA)
        cv2.line(self.image, (x - r, y + r), (x + r, y - r), 30, t, cv2.LINE_AA)

    def tick(self, group, index, value):
        x, y = self.centre(group, index, value)
        r = round(self.layout.bubble_radius * self.sx)
        t = max(2, round(0.3 * self.sx))
        cv2.line(self.image, (x - r, y), (x - r // 3, y + r), 30, t, cv2.LINE_AA)
        cv2.line(self.image, (x - r // 3, y + r), (x + r, y - r), 30, t, cv2.LINE_AA)

    def answers(self, exclude=()):
        for q, a in ANSWERS.items():
            if q not in exclude:
                self.fill("answer", q, a)

    def identity(self, subject="01", roll=ROLL):
        for pos, digit in enumerate(subject):
            self.fill("subject", pos, digit)
        for pos, digit in enumerate(roll):
            self.fill("roll", pos, digit)


def _marked(sheet, **kw):
    config, blank = sheet
    m = Marker(config, blank)
    m.answers(kw.get("exclude", ()))
    m.identity(**{k: v for k, v in kw.items() if k in ("subject", "roll")})
    return m


def _review(result, question, group="answer"):
    return [f for f in result.flags if f.get("question") == str(question) and f.get("group", "answer") == group
            and f["kind"] != "blank"]


def test_clean_sheet_reads_exactly_with_no_flags(sheet):
    m = _marked(sheet)
    result = read_image(m.image, m.config)
    assert result.answers == ANSWERS
    assert result.roll_number == ROLL and result.subject_code == "01"
    assert not result.requires_review, result.flags


@pytest.mark.parametrize("how", ["cross", "tick"])
def test_cross_and_tick_marks_are_flagged(sheet, how):
    m = _marked(sheet, exclude=("10",))
    getattr(m, how)("answer", 10, "B")
    result = read_image(m.image, m.config)
    assert result.requires_review
    assert _review(result, 10), "crossed/ticked bubble must be flagged, never silently accepted"
    assert not _review(result, 11)


def test_overfilled_bubble_is_flagged(sheet):
    m = _marked(sheet, exclude=("20",))
    m.fill("answer", 20, ANSWERS["20"], radius_mm=m.layout.bubble_radius * 1.9)
    result = read_image(m.image, m.config)
    assert _review(result, 20)


def test_half_filled_bubble_is_flagged(sheet):
    m = _marked(sheet, exclude=("30",))
    x, y = m.centre("answer", 30, ANSWERS["30"])
    cv2.ellipse(m.image, (x, y), (round(m.layout.bubble_radius * 0.92 * m.sx),) * 2, 0, 0, 180, 30, -1)
    result = read_image(m.image, m.config)
    assert _review(result, 30)


def test_two_marks_and_blank_are_reported(sheet):
    m = _marked(sheet, exclude=("5", "6"))
    m.fill("answer", 5, "A")
    m.fill("answer", 5, "C")
    result = read_image(m.image, m.config)
    assert result.answers["5"] is None and result.answers["6"] is None
    assert any(f["kind"] == "multiple" and f["question"] == "5" for f in result.flags)
    assert any(f["kind"] == "blank" and f["question"] == "6" for f in result.flags)


def test_roll_number_problems_are_flagged(sheet):
    m = _marked(sheet)
    m.fill("roll", 3, "7")                       # second mark in one roll digit column
    result = read_image(m.image, m.config)
    assert result.roll_number is None
    assert any(f["kind"] == "invalid_id" for f in result.flags)


def test_subject_code_problems_are_flagged(sheet):
    m = _marked(sheet, subject="0")             # second subject digit left blank
    result = read_image(m.image, m.config)
    assert result.subject_code == ""
    assert any("Subject code" in f.get("message", "") for f in result.flags)
    m = _marked(sheet)
    m.fill("subject", 1, "4")                    # two marks in one subject digit
    result = read_image(m.image, m.config)
    assert any("Subject code" in f.get("message", "") for f in result.flags)


def test_subject_code_must_match_the_test_when_configured(sheet):
    config, blank = sheet
    config = {**config, "test": {**config["test"], "subject_code": "02"}}
    m = Marker(config, blank)
    m.answers()
    m.identity()
    result = read_image(m.image, config)
    assert result.subject_code == "01"
    assert any("does not match" in f.get("message", "") for f in result.flags)


def _assert_exact(result):
    assert result.answers == ANSWERS, [q for q in ANSWERS if result.answers.get(q) != ANSWERS[q]][:10]
    assert result.roll_number == ROLL and result.subject_code == "01"
    assert not result.requires_review, result.flags[:5]


@pytest.mark.parametrize("name,transform", [
    ("rot90", lambda im: cv2.rotate(im, cv2.ROTATE_90_CLOCKWISE)),
    ("rot180", lambda im: cv2.rotate(im, cv2.ROTATE_180)),
    ("rot270", lambda im: cv2.rotate(im, cv2.ROTATE_90_COUNTERCLOCKWISE)),
    ("mirror", lambda im: cv2.flip(im, 1)),
    ("150dpi", lambda im: cv2.resize(im, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)),
    ("120dpi", lambda im: cv2.resize(im, None, fx=0.4, fy=0.4, interpolation=cv2.INTER_AREA)),
    ("blur", lambda im: cv2.GaussianBlur(im, (7, 7), 0)),
])
def test_orientation_and_resolution_do_not_matter(sheet, name, transform):
    m = _marked(sheet)
    _assert_exact(read_image(transform(m.image), m.config))


@pytest.mark.parametrize("angle", [-9, -4, 3, 7, 12])
def test_any_tilt_with_margin_reads_exactly(sheet, angle):
    m = _marked(sheet)
    h0, w0 = m.image.shape
    pad = int(0.25 * max(h0, w0))                 # room for the rotated page to stay fully in frame
    padded = cv2.copyMakeBorder(m.image, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=235)
    h, w = padded.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    tilted = cv2.warpAffine(padded, matrix, (w, h), borderValue=200)
    _assert_exact(read_image(tilted, m.config))


def test_photocopy_binary_and_noise(sheet):
    m = _marked(sheet)
    rng = np.random.default_rng(7)
    noisy = np.clip(m.image.astype(np.int16) + rng.normal(0, 10, m.image.shape), 0, 255).astype(np.uint8)
    for im in (cv2.threshold(m.image, 140, 255, cv2.THRESH_BINARY)[1],
               cv2.threshold(noisy, 150, 255, cv2.THRESH_BINARY)[1],
               noisy):
        result = read_image(im, m.config)
        assert result.answers == ANSWERS and result.roll_number == ROLL


def test_strong_shadow_and_uneven_light(sheet):
    m = _marked(sheet)
    h, w = m.image.shape
    x = np.linspace(0, 1, w, dtype=np.float32)[None, :]
    y = np.linspace(0, 1, h, dtype=np.float32)[:, None]
    shade = 1 - 0.55 * np.clip(x * 0.6 + y * 0.5 - 0.3, 0, 1) - 0.3 * np.exp(-((x - .6) ** 2 + (y - .5) ** 2) / .06)
    dim = np.clip(m.image.astype(np.float32) * shade, 0, 255).astype(np.uint8)
    _assert_exact(read_image(dim, m.config))


def test_unreadable_input_fails_loudly_not_silently(sheet):
    config, _blank = sheet
    result = read_image(np.full((900, 700), 255, np.uint8), config)
    assert result.requires_review and any(f["kind"] == "unreadable" for f in result.flags)
    assert result.answers == {}


def _write_name(m, text):
    bx, by, bw, bh, cells = m.layout.name_box
    cell_w = bw / cells
    for i, ch in enumerate(text):
        if ch == " ":
            continue
        scale = 1.3
        (tw, th), _ = cv2.getTextSize(ch, cv2.FONT_HERSHEY_DUPLEX, scale, 2)
        px = round((bx + (i + 0.5) * cell_w) * m.sx - tw / 2)
        py = round((by + bh * 0.5) * m.sy + th / 2)
        cv2.putText(m.image, ch, (px, py), cv2.FONT_HERSHEY_DUPLEX, scale, 30, 4, cv2.LINE_AA)


@pytest.mark.parametrize("name", ["SWETHA JARPULA", "RAMESH KUMAR", "ANITA"])
def test_handwritten_name_is_read_cell_by_cell(sheet, name):
    m = _marked(sheet)
    _write_name(m, name)
    result = read_image(m.image, m.config)
    assert result.student_name == name
    assert result.thresholds.get("name_confidence", 0) > 0.5


def test_name_survives_rotation_and_low_resolution(sheet):
    m = _marked(sheet)
    _write_name(m, "RAMESH KUMAR")
    small = cv2.resize(cv2.rotate(m.image, cv2.ROTATE_180), None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    assert read_image(small, m.config).student_name == "RAMESH KUMAR"


def test_blank_name_box_gives_empty_name(sheet):
    m = _marked(sheet)
    assert read_image(m.image, m.config).student_name == ""


def test_thin_vertical_stroke_is_read_as_I(sheet):
    m = _marked(sheet)
    bx, by, bw, bh, cells = m.layout.name_box
    cell_w = bw / cells
    for i, ch in enumerate("ALIK"):
        if ch == "I":
            x = round((bx + (i + 0.5) * cell_w) * m.sx)
            cv2.line(m.image, (x, round((by + bh * 0.22) * m.sy)), (x, round((by + bh * 0.78) * m.sy)), 30, 4, cv2.LINE_AA)
        else:
            _write_name_char(m, ch, i)
    assert read_image(m.image, m.config).student_name == "ALIK"


def _write_name_char(m, ch, i):
    bx, by, bw, bh, cells = m.layout.name_box
    (tw, th), _ = cv2.getTextSize(ch, cv2.FONT_HERSHEY_DUPLEX, 1.3, 2)
    cv2.putText(m.image, ch, (round((bx + (i + 0.5) * bw / cells) * m.sx - tw / 2), round((by + bh * 0.5) * m.sy + th / 2)),
                cv2.FONT_HERSHEY_DUPLEX, 1.3, 30, 4, cv2.LINE_AA)


@pytest.mark.parametrize("rotation", [None, cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_180])
def test_heavy_blur_still_finds_orientation(sheet, rotation):
    m = _marked(sheet)
    image = cv2.GaussianBlur(cv2.resize(m.image, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA), (9, 9), 0)
    if rotation is not None:
        image = cv2.rotate(image, rotation)
    result = read_image(image, m.config)
    assert result.answers == ANSWERS and result.roll_number == ROLL


@pytest.mark.parametrize("shape", ["half", "crescent", "three_quarter", "small_dot", "light_grey", "diagonal_slash"])
def test_every_kind_of_partial_fill_is_flagged(sheet, shape):
    m = _marked(sheet, exclude=("12",))
    answer = ANSWERS["12"]
    x, y = m.centre("answer", 12, answer)
    r = round(m.layout.bubble_radius * 0.92 * m.sx)
    if shape == "half":
        cv2.ellipse(m.image, (x, y), (r, r), 0, 0, 180, 30, -1, cv2.LINE_AA)
    elif shape == "crescent":
        cv2.circle(m.image, (x, y), r, 30, -1, cv2.LINE_AA)
        cv2.circle(m.image, (x - r // 2, y - r // 3), round(r * 0.75), 255, -1, cv2.LINE_AA)
    elif shape == "three_quarter":
        cv2.ellipse(m.image, (x, y), (r, r), 0, -45, 225, 30, -1, cv2.LINE_AA)
    elif shape == "small_dot":
        cv2.circle(m.image, (x, y), max(2, round(r * 0.35)), 30, -1, cv2.LINE_AA)
    elif shape == "ring_only":
        cv2.circle(m.image, (x, y), r, 30, max(2, round(0.35 * m.sx)), cv2.LINE_AA)
    elif shape == "light_grey":
        cv2.circle(m.image, (x, y), r, 175, -1, cv2.LINE_AA)
    else:
        cv2.line(m.image, (x - r, y + r), (x + r, y - r), 30, max(3, round(0.6 * m.sx)), cv2.LINE_AA)
    result = read_image(m.image, m.config)
    assert _review(result, 12) or any(f["kind"] == "multiple" and f["question"] == "12" for f in result.flags), \
        f"{shape} fill must never pass as a clean answer"
    assert result.requires_review


def test_properly_filled_bubble_of_every_size_stays_clean(sheet):
    for scale in (0.9, 1.0, 1.1):
        m = _marked(sheet, exclude=("12",))
        m.fill("answer", 12, ANSWERS["12"], radius_mm=m.layout.bubble_radius * scale)
        result = read_image(m.image, m.config)
        assert result.answers["12"] == ANSWERS["12"] and not result.requires_review, (scale, result.flags[:3])


@pytest.mark.parametrize("fx", [1.0, 0.5, 0.4])
def test_partial_fill_flagged_at_every_resolution(sheet, fx):
    m = _marked(sheet, exclude=("12",))
    x, y = m.centre("answer", 12, ANSWERS["12"])
    r = round(m.layout.bubble_radius * 0.92 * m.sx)
    cv2.ellipse(m.image, (x, y), (r, r), 0, 90, 270, 30, -1, cv2.LINE_AA)
    image = cv2.resize(m.image, None, fx=fx, fy=fx, interpolation=cv2.INTER_AREA) if fx != 1.0 else m.image
    assert _review(read_image(image, m.config), 12)
