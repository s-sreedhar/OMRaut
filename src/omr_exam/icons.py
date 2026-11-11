"""Small built-in line-icon set, rendered from inline SVG so no image files ship with the app."""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

# 24x24 viewBox, drawn with a 2px round stroke in the requested colour.
_PATHS: dict[str, str] = {
    "home": '<path d="M3 11l9-8 9 8"/><path d="M5 9.5V21h5v-6h4v6h5V9.5"/>',
    "tests": '<path d="M6 2h9l5 5v15H6z"/><path d="M15 2v5h5"/><path d="M9 13h8M9 17h5"/>',
    "students": '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 21c0-3.6 2.9-6.5 6.5-6.5s6.5 2.900 6.500 6.500"/>'
                '<path d="M16 4.700a3.500 3.500 0 0 1 0 6.600M18.500 14.800c1.900 1 3 3 3 6.200"/>',
    "scan": '<path d="M3 8V5a2 2 0 0 1 2-2h3M16 3h3a2 2 0 0 1 2 2v3M21 16v3a2 2 0 0 1-2 2h-3M8 21H5a2 2 0 0 1-2-2v-3"/>'
            '<path d="M3 12h18"/>',
    "review": '<path d="M9 4H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V5a1 1 0 0 0-1-1h-3"/>'
              '<rect x="9" y="2" width="6" height="4" rx="1"/><path d="M9 14l2 2 4-4.500"/>',
    "results": '<path d="M4 21V10M10 21V4M16 21v-8M22 21H2"/>',
    "settings": '<path d="M4 6h10M18 6h2M4 12h3M11 12h9M4 18h11M19 18h1"/>'
                '<circle cx="16" cy="6" r="2"/><circle cx="9" cy="12" r="2"/><circle cx="17" cy="18" r="2"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "upload": '<path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>',
    "download": '<path d="M12 4v12M7 11l5 5 5-5"/><path d="M4 16v3a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-3"/>',
    "folder": '<path d="M3 6a2 2 0 0 1 2-2h4l2 3h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "printer": '<path d="M7 8V3h10v5"/><rect x="3" y="8" width="18" height="9" rx="2"/><rect x="7" y="14" width="10" height="7"/>',
    "key": '<circle cx="8" cy="15" r="4.500"/><path d="M11.300 11.700L20 3M16 7l3 3M13.500 9.500l2 2"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.800-4.800"/>',
    "alert": '<path d="M12 3L2 20h20z"/><path d="M12 10v4.500M12 17.500v.100"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.500v.100"/>',
    "check": '<path d="M4 12.500l5 5L20 6.500"/>',
    "check-circle": '<circle cx="12" cy="12" r="9"/><path d="M8 12.500l3 3 5-6"/>',
    "x": '<path d="M6 6l12 12M18 6L6 18"/>',
    "x-circle": '<circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/>',
    "chevron-down": '<path d="M6 9l6 6 6-6"/>',
    "chevron-up": '<path d="M6 15l6-6 6 6"/>',
    "chevron-right": '<path d="M9 6l6 6-6 6"/>',
    "chevron-left": '<path d="M15 6l-6 6 6 6"/>',
    "arrow-right": '<path d="M4 12h16M14 6l6 6-6 6"/>',
    "lock": '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7.500a4 4 0 0 1 8 0V11"/>',
    "zoom-in": '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.800-4.800M8 11h6M11 8v6"/>',
    "zoom-out": '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.800-4.800M8 11h6"/>',
    "fit": '<path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5"/>',
    "eye": '<path d="M2 12s3.600-7 10-7 10 7 10 7-3.600 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    "list": '<path d="M8 6h13M8 12h13M8 18h13M3.500 6h.100M3.500 12h.100M3.500 18h.100"/>',
    "grid": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/>',
    "more": '<path d="M5 12h.100M12 12h.100M19 12h.100"/>',
    "trash": '<path d="M4 7h16M9 7V4h6v3M6 7l1 14h10l1-14M10 11v6M14 11v6"/>',
    "edit": '<path d="M4 20h4L20 8l-4-4L4 16z"/><path d="M13.500 6.500l4 4"/>',
    "refresh": '<path d="M20 5v5h-5"/><path d="M19.400 10A8 8 0 1 0 20 14"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "database": '<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v6c0 1.700 3.600 3 8 3s8-1.300 8-3V6M4 12v6c0 1.700 3.600 3 8 3s8-1.300 8-3v-6"/>',
    "layers": '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>',
    "sheet": '<rect x="5" y="2.500" width="14" height="19" rx="2"/><circle cx="9.500" cy="8.500" r="1.200"/><circle cx="14.500" cy="8.500" r="1.200"/>'
             '<circle cx="9.500" cy="13" r="1.200"/><circle cx="14.500" cy="13" r="1.200"/><path d="M9 17.500h6"/>',
    "user-plus": '<circle cx="10" cy="8" r="3.500"/><path d="M3 21c0-3.600 3-6.500 7-6.500 1.400 0 2.700.400 3.800 1M18 14v6M15 17h6"/>',
    "keyboard": '<rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.100M10 10h.100M14 10h.100M18 10h.100M7 14h10"/>',
    "shield": '<path d="M12 3l8 3v6c0 4.500-3.200 8-8 9-4.800-1-8-4.500-8-9V6z"/><path d="M9 12l2 2 4-4.500"/>',
    "file-export": '<path d="M13 3H6v18h12V8z"/><path d="M13 3v5h5"/><path d="M9 14h6M12.500 11.500L15 14l-2.500 2.500"/>',
}


def _svg(name: str, color: str, stroke: float) -> bytes:
    body = _PATHS[name]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
            f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>').encode()


@lru_cache(maxsize=512)
def pixmap(name: str, color: str = "#3A433E", size: int = 18, scale: float = 2.0, stroke: float = 1.9) -> QPixmap:
    """Render an icon to a pixmap; ``scale`` is the device pixel ratio baked into it."""
    pixels = max(1, round(size * scale))
    image = QPixmap(pixels, pixels)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(QByteArray(_svg(name, color, stroke))).render(painter, QRectF(0, 0, pixels, pixels))
    painter.end()
    image.setDevicePixelRatio(scale)
    return image


@lru_cache(maxsize=512)
def icon(name: str, color: str = "#3A433E", size: int = 18, disabled: str = "#B3B8B1") -> QIcon:
    """A crisp icon at 1x-3x display scaling, with a muted variant for disabled buttons."""
    result = QIcon()
    for scale in (1.0, 2.0, 3.0):
        result.addPixmap(pixmap(name, color, size, scale), QIcon.Normal)
        result.addPixmap(pixmap(name, disabled, size, scale), QIcon.Disabled)
    return result


def icon_size(size: int = 18) -> QSize:
    return QSize(size, size)


def has_icon(name: str) -> bool:
    return name in _PATHS


# ------------------------------------------------------------------ application logo
APP_LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#1A9A8E"/><stop offset="1" stop-color="#0B5C56"/></linearGradient></defs>
<rect x="8" y="8" width="240" height="240" rx="58" fill="url(#g)"/>
<g fill="none" stroke="#FFFFFF" stroke-width="15" stroke-opacity="0.9">
<circle cx="88" cy="88" r="27"/><circle cx="168" cy="88" r="27"/><circle cx="88" cy="168" r="27"/></g>
<circle cx="168" cy="168" r="36" fill="#FFFFFF"/>
<path d="M150 169 l13 13 l24 -27" fill="none" stroke="#0B5C56" stroke-width="13"
 stroke-linecap="round" stroke-linejoin="round"/>
</svg>"""


def app_logo(size: int, scale: float = 1.0):
    """The OMRaut mark (a bubble grid with one confirmed answer) as a pixmap."""
    from PySide6.QtCore import QByteArray, QRectF, Qt
    from PySide6.QtGui import QPainter, QPixmap
    from PySide6.QtSvg import QSvgRenderer

    pixels = max(1, round(size * scale))
    image = QPixmap(pixels, pixels)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(QByteArray(APP_LOGO_SVG.encode("utf-8"))).render(painter, QRectF(0, 0, pixels, pixels))
    painter.end()
    image.setDevicePixelRatio(scale)
    return image


def app_icon():
    from PySide6.QtGui import QIcon

    result = QIcon()
    for size in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        result.addPixmap(app_logo(size))
    return result
