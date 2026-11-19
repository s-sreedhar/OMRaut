"""Train the tiny handwritten-letter classifier used for the NAME box (developer tool, needs torch + Pillow).

The model reads one block letter (A-Z) per cell.  It is trained on synthetic handwriting: A-Z (and a little
lower-case) rendered from many fonts, then heavily distorted (rotation, shear, stroke width, wobble, blur,
noise).  Output: src/omr_exam/data/name_model.npz, evaluated in pure numpy by ``omr_exam.nameocr``.

    python tools/train_name_model.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from omr_exam.nameocr import normalise_glyph  # noqa: E402

FONT_DIR = Path("C:/Windows/Fonts")
FONTS = ["Inkfree.ttf", "segoepr.ttf", "segoeprb.ttf", "comic.ttf", "comicbd.ttf", "comici.ttf", "comicz.ttf",
         "Candara.ttf", "corbel.ttf", "calibri.ttf", "arial.ttf", "tahoma.ttf", "verdana.ttf", "trebuc.ttf",
         "segoeui.ttf", "framd.ttf", "Gabriola.ttf", "segoesc.ttf", "consola.ttf"]
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
EMNIST_SAMPLES = 60000
rng = np.random.default_rng(1234)


def render(char: str, font: ImageFont.FreeTypeFont) -> np.ndarray:
    canvas = Image.new("L", (128, 128), 0)
    ImageDraw.Draw(canvas).text((64, 64), char, font=font, fill=255, anchor="mm", stroke_width=int(rng.integers(0, 4)),
                                stroke_fill=255)
    return np.array(canvas)


HERSHEY = [cv2.FONT_HERSHEY_SIMPLEX, cv2.FONT_HERSHEY_DUPLEX, cv2.FONT_HERSHEY_COMPLEX, cv2.FONT_HERSHEY_TRIPLEX,
           cv2.FONT_HERSHEY_SCRIPT_SIMPLEX, cv2.FONT_HERSHEY_SCRIPT_COMPLEX, cv2.FONT_HERSHEY_COMPLEX_SMALL,
           cv2.FONT_HERSHEY_SIMPLEX | cv2.FONT_ITALIC, cv2.FONT_HERSHEY_COMPLEX | cv2.FONT_ITALIC]


def render_pen(char: str) -> np.ndarray:
    """Single-stroke pen letters (pointed apexes, open tops) at a random pen width."""
    face = HERSHEY[int(rng.integers(0, len(HERSHEY)))]
    scale = 2.6
    (w, h), base = cv2.getTextSize(char, face, scale, 2)
    canvas = np.zeros((128, 128), np.uint8)
    cv2.putText(canvas, char, (64 - w // 2, 64 + h // 2), face, scale, 255, int(rng.integers(2, 8)), cv2.LINE_AA)
    return canvas


def distort(img: np.ndarray) -> np.ndarray:
    h, w = img.shape
    angle = rng.normal(0, 7)
    shear = rng.normal(0, 0.12)
    sx, sy = rng.uniform(0.75, 1.2), rng.uniform(0.8, 1.2)
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    m[0, 1] += shear
    m[0, 0] *= sx
    m[1, 1] *= sy
    out = cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_LINEAR)
    # smooth wobble, like a shaky pen
    dx = cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), 9) * rng.uniform(15, 60)
    dy = cv2.GaussianBlur(rng.normal(0, 1, (h, w)).astype(np.float32), (0, 0), 9) * rng.uniform(15, 60)
    gx, gy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    out = cv2.remap(out, gx + dx, gy + dy, cv2.INTER_LINEAR)
    k = int(rng.integers(0, 7))                      # pen thickness
    if k:
        out = cv2.dilate(out, np.ones((k, k), np.uint8))
    elif rng.random() < 0.3:
        out = cv2.erode(out, np.ones((2, 2), np.uint8))
    out = cv2.GaussianBlur(out, (0, 0), rng.uniform(0.3, 1.4))
    return out


def render_arch_a() -> np.ndarray:
    """Hand-drawn 'A' variants: rounded or pointed apex, vertical or splayed legs, high or low crossbar."""
    canvas = np.zeros((128, 128), np.uint8)
    w, h = rng.uniform(38, 70), rng.uniform(66, 96)
    t = int(rng.integers(3, 9))
    cx, base = 64 + rng.normal(0, 3), 64 + h / 2
    top = base - h
    round_top = rng.random()
    spread = rng.uniform(0.0, 0.25)
    lx, rx = cx - w / 2 - spread * w, cx + w / 2 + spread * w
    ax, bx = cx - w / 2, cx + w / 2
    arc_h = (0.1 + 0.9 * round_top) * w / 2
    cv2.line(canvas, (int(lx), int(base)), (int(ax), int(top + arc_h)), 255, t, cv2.LINE_AA)
    cv2.line(canvas, (int(rx), int(base)), (int(bx), int(top + arc_h)), 255, t, cv2.LINE_AA)
    cv2.ellipse(canvas, (int(cx), int(top + arc_h)), (int(w / 2), int(max(2, arc_h))), 0, 180, 360, 255, t, cv2.LINE_AA)
    by = top + h * rng.uniform(0.52, 0.78)
    frac = (by - top) / h
    cv2.line(canvas, (int(lx + (ax - lx) * (1 - (base - by) / max(1, base - top - arc_h))), int(by)),
             (int(rx + (bx - rx) * (1 - (base - by) / max(1, base - top - arc_h))), int(by)), 255, max(2, t - 1), cv2.LINE_AA)
    return canvas


def build(per_font: int = 90):
    xs, ys = [], []
    fonts = []
    for name in FONTS:
        path = FONT_DIR / name
        if path.exists():
            fonts.append(ImageFont.truetype(str(path), 84))
    print("fonts:", len(fonts))
    for label, letter in enumerate(LETTERS):
        for font in fonts:
            variants = [(letter, 1.0), (letter.lower(), 0.25)]
            for char, share in variants:
                base = render(char, font)
                if base.max() == 0:
                    continue
                for _ in range(int(per_font * share)):
                    g = normalise_glyph(distort(base) > rng.integers(60, 140))
                    if g is not None:
                        xs.append(g)
                        ys.append(label)
    for label, letter in enumerate(LETTERS):               # pen-stroke letters: many more draws, they are the closest style
        for _ in range(per_font * 10):
            g = normalise_glyph(distort(render_pen(letter if rng.random() < 0.85 else letter.lower())) > rng.integers(50, 130))
            if g is not None:
                xs.append(g)
                ys.append(label)
    for _ in range(per_font * 25):                        # rounded / pointed hand-drawn A
        g = normalise_glyph(distort(render_arch_a()) > rng.integers(50, 130))
        if g is not None:
            xs.append(g)
            ys.append(0)
    emnist = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if emnist is not None:                                 # real handwriting: EMNIST Letters (NIST SD19), ~125k samples
        from torchvision.datasets import EMNIST
        data = EMNIST(str(emnist), split="letters", train=True, download=False)
        images = data.data.numpy().transpose(0, 2, 1)       # stored transposed
        labels = data.targets.numpy() - 1                   # 1..26 -> 0..25, upper+lower merged
        pick = rng.permutation(len(images))[:EMNIST_SAMPLES]
        for i in pick:
            big = cv2.resize(images[i], (128, 128), interpolation=cv2.INTER_CUBIC)
            for variant in (big, distort(big)):
                g = normalise_glyph(variant > rng.integers(50, 120))
                if g is not None:
                    xs.append(g)
                    ys.append(int(labels[i]))
        print("emnist samples:", len(pick))
    real = ROOT / "tools" / "real_letters.npz"             # a few real labelled handwriting samples
    if real.exists():
        with np.load(real) as data:
            for img, lab in zip(data["images"], data["labels"]):
                for _ in range(per_font * 3):
                    g = normalise_glyph(distort(cv2.resize(img, (128, 128))) > rng.integers(60, 130))
                    if g is not None:
                        xs.append(g)
                        ys.append(LETTERS.index(str(lab)))
    return np.array(xs, np.float32)[:, None], np.array(ys, np.int64)


def main() -> None:
    import torch
    from torch import nn

    x, y = build()
    order = rng.permutation(len(x))
    x, y = x[order], y[order]
    split = int(len(x) * 0.93)
    xt, yt, xv, yv = map(torch.from_numpy, (x[:split], y[:split], x[split:], y[split:]))
    net = nn.Sequential(
        nn.Conv2d(1, 16, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
        nn.Conv2d(16, 32, 3, padding=1), nn.ReLU(), nn.MaxPool2d(2),
        nn.Flatten(), nn.Dropout(0.3), nn.Linear(32 * 7 * 7, 128), nn.ReLU(), nn.Linear(128, 26))
    opt = torch.optim.Adam(net.parameters(), 2e-3)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, 14)
    for epoch in range(14):
        net.train()
        perm = torch.randperm(len(xt))
        for i in range(0, len(xt), 128):
            idx = perm[i:i + 128]
            loss = nn.functional.cross_entropy(net(xt[idx]), yt[idx], label_smoothing=0.05)
            opt.zero_grad(); loss.backward(); opt.step()
        sched.step()
        net.eval()
        with torch.no_grad():
            acc = (net(xv).argmax(1) == yv).float().mean().item()
        print(f"epoch {epoch + 1} val accuracy {acc:.3f}")
    held = ROOT / "tools" / "real_letters_eval.npz"           # real sheet never used for training
    if held.exists():
        with np.load(held) as data:
            gl = [normalise_glyph(cv2.resize(i, (128, 128)) > 100) for i in data["images"]]
            truth = [LETTERS.index(str(c)) for c in data["labels"]]
        with torch.no_grad():
            pred = net(torch.from_numpy(np.array(gl, np.float32)[:, None])).argmax(1).tolist()
        print("held-out real handwriting:", "".join(LETTERS[i] for i in pred), "correct", sum(a == b for a, b in zip(pred, truth)), "/", len(truth))
    state = {k: v.detach().numpy().astype(np.float32) for k, v in net.state_dict().items()}
    out = ROOT / "src" / "omr_exam" / "data"
    out.mkdir(exist_ok=True)
    np.savez_compressed(out / "name_model.npz", c1w=state["0.weight"], c1b=state["0.bias"], c2w=state["3.weight"],
                        c2b=state["3.bias"], f1w=state["8.weight"],
                        f1b=state["8.bias"],
                        f2w=state["10.weight"], f2b=state["10.bias"])
    print("saved", out / "name_model.npz")


if __name__ == "__main__":
    main()
