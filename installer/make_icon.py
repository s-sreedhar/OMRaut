"""Regenerate installer/OMRaut.ico from the logo in omr_exam.icons (run after changing the logo)."""
import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QGuiApplication

from omr_exam.icons import app_logo

app = QGuiApplication([])
sizes = (16, 20, 24, 32, 40, 48, 64, 128, 256)
images = []
for size in sizes:
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    app_logo(size).toImage().save(buffer, "PNG")
    images.append(bytes(buffer.data()))
header = struct.pack("<HHH", 0, 1, len(sizes))
offset = 6 + 16 * len(sizes)
entries = b""
for size, data in zip(sizes, images):
    entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
    offset += len(data)
target = Path(__file__).with_name("OMRaut.ico")
target.write_bytes(header + entries + b"".join(images))
print(f"wrote {target} ({target.stat().st_size} bytes)")
