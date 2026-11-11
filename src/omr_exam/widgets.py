"""Reusable interface components shared by every screen."""
from __future__ import annotations

from typing import Any, Callable, Iterable

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QFrame, QGraphicsScene, QGraphicsView, QHBoxLayout, QHeaderView, QLabel,
    QPushButton, QSizePolicy, QStyle, QStyledItemDelegate, QTableWidget, QToolTip, QVBoxLayout, QWidget,
)

from omr_exam import theme
from omr_exam.icons import icon, icon_size, pixmap


# ------------------------------------------------------------------ primitives
def label(text: str = "", name: str = "", wrap: bool = True) -> QLabel:
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def button(text: str, handler: Callable[[], Any] | None = None, kind: str = "", icon_name: str = "",
           tip: str = "") -> QPushButton:
    """A push button. ``kind`` is '', 'primary', 'danger', 'ghost', 'link' or 'tool'."""
    widget = QPushButton(f" {text}" if icon_name and text else text)
    if kind:
        widget.setObjectName(kind)
    if icon_name:
        color = {"primary": "#FFFFFF", "danger": theme.BAD, "link": theme.ACCENT}.get(kind, theme.TEXT_2)
        widget.setIcon(icon(icon_name, color, 16))
        widget.setIconSize(icon_size(16))
    if kind not in {"tool"}:
        widget.setCursor(Qt.PointingHandCursor)
    if tip:
        widget.setToolTip(tip)
        if not text:
            widget.setAccessibleName(tip)
    if handler is not None:
        widget.clicked.connect(lambda _checked=False: handler())
    return widget


def divider() -> QFrame:
    line = QFrame()
    line.setObjectName("divider")
    line.setFixedHeight(1)
    return line


def kbd(text: str) -> QLabel:
    return label(text, "kbd", wrap=False)


def card(margins: tuple[int, int, int, int] = (18, 16, 18, 16), spacing: int = 10) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return frame, layout


def icon_tile(name: str, tone: str = "info", size: int = 36) -> QLabel:
    tile = QLabel()
    tile.setObjectName("iconTile")
    tile.setProperty("tone", tone)
    tile.setFixedSize(size, size)
    tile.setAlignment(Qt.AlignCenter)
    tile.setPixmap(pixmap(name, theme.TONES[tone][0], round(size * 0.52)))
    return tile


def table(headers: list[str], stretch_column: int | None = None, editable: bool = False) -> QTableWidget:
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.setAlternatingRowColors(False)
    widget.setShowGrid(False)
    widget.verticalHeader().setVisible(False)
    widget.verticalHeader().setDefaultSectionSize(38)
    widget.setSelectionBehavior(QAbstractItemView.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SingleSelection)
    widget.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
    widget.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
    widget.setTabKeyNavigation(False)
    widget.setWordWrap(False)
    widget.setItemDelegate(TintDelegate(widget))
    if not editable:
        widget.setEditTriggers(QAbstractItemView.NoEditTriggers)
    header = widget.horizontalHeader()
    header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    header.setHighlightSections(False)
    header.setMinimumSectionSize(48)
    if stretch_column is not None:
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(stretch_column, QHeaderView.Stretch)
    return widget


# ------------------------------------------------------------------ status
class Pill(QLabel):
    """Small rounded status label. Always carries text, never colour alone."""

    def __init__(self, text: str = "", tone: str = "neutral", parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setObjectName("pill")
        self.setProperty("tone", tone)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)

    def set(self, text: str, tone: str = "neutral") -> None:
        self.setText(text)
        theme.set_property(self, "tone", tone)


_BANNER_ICONS = {"info": "info", "good": "check-circle", "warn": "alert", "bad": "x-circle", "neutral": "info"}


class Banner(QFrame):
    """Inline message with an icon, optional action button, and a tone."""

    def __init__(self, text: str = "", tone: str = "info", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("banner")
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 9, 12, 9)
        row.setSpacing(10)
        self._icon = QLabel()
        self._icon.setFixedSize(18, 18)
        self._text = label(text)
        self._text.setTextFormat(Qt.RichText)
        self._action = button("", None, "")
        self._action.hide()
        self._handler: Callable[[], Any] | None = None
        self._action.clicked.connect(lambda _c=False: self._handler and self._handler())
        row.addWidget(self._icon, 0, Qt.AlignTop)
        row.addWidget(self._text, 1)
        row.addWidget(self._action, 0, Qt.AlignVCenter)
        self._tone = ""
        self.set_tone(tone)

    def set_tone(self, tone: str) -> None:
        if tone == self._tone:
            return
        self._tone = tone
        self._icon.setPixmap(pixmap(_BANNER_ICONS.get(tone, "info"), theme.TONES[tone][0], 18))
        theme.set_property(self, "tone", tone)
        theme.repolish(self._text)

    def set(self, text: str, tone: str | None = None) -> None:
        self._text.setText(text)
        self.setAccessibleName(text)
        if tone is not None:
            self.set_tone(tone)

    def setText(self, text: str) -> None:  # noqa: N802 - QLabel-compatible
        self.set(text)

    def text(self) -> str:
        return self._text.text()

    def set_action(self, text: str = "", handler: Callable[[], Any] | None = None) -> None:
        self._handler = handler
        self._action.setText(text)
        self._action.setVisible(bool(text and handler))


class StatTile(QFrame):
    """A headline number with a caption; a number alone is the right form for a single value."""

    def __init__(self, caption: str, icon_name: str, tone: str = "info", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("card")
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 14, 16, 14)
        row.setSpacing(12)
        self._tile = icon_tile(icon_name, tone)
        self._icon_name = icon_name
        row.addWidget(self._tile, 0, Qt.AlignTop)
        column = QVBoxLayout()
        column.setSpacing(0)
        self.value = label("0", "statValue", wrap=False)
        self.caption = label(caption, "statLabel", wrap=False)
        self.sub = label("", "statSub", wrap=False)
        self.sub.hide()
        column.addWidget(self.value)
        column.addWidget(self.caption)
        column.addWidget(self.sub)
        column.addStretch(1)
        row.addLayout(column, 1)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(92)
        self.setAccessibleName(caption)

    def set_value(self, value: str, sub: str = "", tone: str | None = None) -> None:
        self.value.setText(value)
        self.sub.setText(sub)
        self.sub.setVisible(bool(sub))
        self.setAccessibleDescription(f"{value} {sub}".strip())
        if tone is not None and tone != self._tile.property("tone"):
            self._tile.setPixmap(pixmap(self._icon_name, theme.TONES[tone][0], 19))
            theme.set_property(self._tile, "tone", tone)

    # QLabel-compatible shims so existing callers can treat a tile like its value label.
    def setText(self, value: str) -> None:  # noqa: N802
        self.set_value(value)

    def text(self) -> str:
        return self.value.text()


class EmptyState(QWidget):
    """Shown in place of a table or panel that has nothing to display yet."""

    def __init__(self, icon_name: str, title: str, body: str, actions: Iterable[QPushButton] = (),
                 parent: QWidget | None = None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 28, 24, 28)
        outer.setSpacing(8)
        outer.addStretch(1)
        outer.addWidget(icon_tile(icon_name, "neutral", 52), 0, Qt.AlignHCenter)
        outer.addSpacing(4)
        self.title = label(title, "emptyTitle")
        self.title.setAlignment(Qt.AlignHCenter)
        self.body = label(body, "emptyBody")
        self.body.setAlignment(Qt.AlignHCenter)
        outer.addWidget(self.title)
        outer.addWidget(self.body)
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch(1)
        for action in actions:
            row.addWidget(action)
        row.addStretch(1)
        outer.addSpacing(6)
        outer.addLayout(row)
        outer.addStretch(1)

    def set(self, title: str, body: str) -> None:
        self.title.setText(title)
        self.body.setText(body)


class Segmented(QFrame):
    """Mutually exclusive view switch (two to four options)."""

    changed = Signal(str)

    def __init__(self, options: Iterable[tuple[str, str]], parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("segmented")
        row = QHBoxLayout(self)
        row.setContentsMargins(3, 3, 3, 3)
        row.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for key, text in options:
            option = QPushButton(text)
            option.setObjectName("segment")
            option.setCheckable(True)
            option.setCursor(Qt.PointingHandCursor)
            option.clicked.connect(lambda _c=False, k=key: self.changed.emit(k))
            self._group.addButton(option)
            self._buttons[key] = option
            row.addWidget(option)
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        first = next(iter(self._buttons.values()), None)
        if first is not None:
            first.setChecked(True)

    def current(self) -> str:
        return next((key for key, option in self._buttons.items() if option.isChecked()), "")

    def set_current(self, key: str) -> None:
        if key in self._buttons:
            self._buttons[key].setChecked(True)
        elif not key:                        # nothing chosen yet
            self._group.setExclusive(False)
            for option in self._buttons.values():
                option.setChecked(False)
            self._group.setExclusive(True)

    def keys(self) -> list[str]:
        return list(self._buttons)

    def set_text(self, key: str, text: str) -> None:
        self._buttons[key].setText(text)
        self._buttons[key].updateGeometry()
        self.updateGeometry()


class Toast(QFrame):
    """Brief, non-blocking confirmation shown over the bottom of the window."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("toast")
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 10, 16, 10)
        row.setSpacing(10)
        self._icon = QLabel()
        self._text = QLabel()
        row.addWidget(self._icon)
        row.addWidget(self._text)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self.message = ""
        self.hide()
        parent.installEventFilter(self)

    def show_message(self, text: str, tone: str = "good", milliseconds: int = 4000) -> None:
        self.message = text
        colour = {"good": "#4ADE80", "warn": "#FBBF24", "bad": "#F87171"}.get(tone, "#7DD3C7")
        self._icon.setPixmap(pixmap(_BANNER_ICONS.get(tone, "info"), colour, 18))
        self._text.setText(text)
        self.setAccessibleName(text)
        self.adjustSize()
        self._place()
        self.show()
        self.raise_()
        self._timer.start(milliseconds)

    def _place(self) -> None:
        host = self.parentWidget()
        if host is not None:
            self.move(max(12, (host.width() - self.width()) // 2), host.height() - self.height() - 28)

    def eventFilter(self, watched, event):  # noqa: N802
        if event.type() == QEvent.Resize and self.isVisible():
            self._place()
        return False


class DropZone(QFrame):
    """Dashed target that accepts dragged files/folders; content is supplied by the caller."""

    dropped = Signal(list)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._hover = False
        self._enabled_look = True
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(24, 26, 24, 26)
        self.layout_.setSpacing(8)

    def set_active_look(self, enabled: bool) -> None:
        self._enabled_look = enabled
        self.setAcceptDrops(enabled)
        self.update()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setBrush(QColor(theme.ACCENT_SOFT if self._hover else theme.CARD))
        pen = QPen(QColor(theme.ACCENT if self._hover else theme.BORDER_STRONG), 2 if self._hover else 1.5)
        pen.setStyle(Qt.DashLine)
        painter.setPen(pen)
        painter.drawRoundedRect(rect, 10, 10)

    def dragEnterEvent(self, event):  # noqa: N802
        if self._enabled_look and event.mimeData().hasUrls():
            self._hover = True
            self.update()
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):  # noqa: N802
        self._hover = False
        self.update()

    def dropEvent(self, event):  # noqa: N802
        self._hover = False
        self.update()
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            event.acceptProposedAction()
            self.dropped.emit(paths)


# ------------------------------------------------------------------ workflow stepper
class Stepper(QWidget):
    """Horizontal progress through the exam workflow. States: done, current, attention, todo."""

    clicked = Signal(int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._steps: list[dict[str, str]] = []
        self._hover = -1
        self.setMinimumHeight(84)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_steps(self, steps: list[dict[str, str]]) -> None:
        self._steps = steps
        self.setAccessibleName("Workflow progress")
        self.setAccessibleDescription("; ".join(
            f"{index}. {step['title']}: {step.get('caption', '')} ({step['state']})" for index, step in enumerate(steps, 1)))
        self.update()

    def steps(self) -> list[dict[str, str]]:
        return self._steps

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(720, 84)

    def _index_at(self, x: float) -> int:
        if not self._steps:
            return -1
        return min(len(self._steps) - 1, max(0, int(x / (self.width() / len(self._steps)))))

    def mouseMoveEvent(self, event):  # noqa: N802
        index = self._index_at(event.position().x())
        if index != self._hover:
            self._hover = index
            self.setCursor(Qt.PointingHandCursor if index >= 0 else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, event):  # noqa: N802
        self._hover = -1
        self.update()

    def mouseReleaseEvent(self, event):  # noqa: N802
        index = self._index_at(event.position().x())
        if index >= 0:
            self.clicked.emit(index)

    def paintEvent(self, event):  # noqa: N802
        if not self._steps:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        count = len(self._steps)
        slot = self.width() / count
        radius, cy = 13, 18
        title_font = QFont(self.font())
        title_font.setPixelSize(13)
        title_font.setWeight(QFont.DemiBold)
        caption_font = QFont(self.font())
        caption_font.setPixelSize(12)
        for index, step in enumerate(self._steps):
            cx = slot * index + slot / 2
            state = step["state"]
            if index < count - 1:
                done = state == "done"
                painter.setPen(QPen(QColor(theme.GOOD if done else theme.BORDER_STRONG), 2))
                painter.drawLine(QPointF(cx + radius + 6, cy), QPointF(cx + slot - radius - 6, cy))
            fill, ring, ink = {
                "done": (theme.GOOD, theme.GOOD, "#FFFFFF"),
                "current": (theme.ACCENT, theme.ACCENT, "#FFFFFF"),
                "attention": ("#D97706", "#D97706", "#FFFFFF"),
            }.get(state, (theme.CARD, theme.BORDER_STRONG, theme.MUTED))
            if index == self._hover:
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(theme.SUBTLE))
                painter.drawRoundedRect(QRectF(slot * index + 4, 0, slot - 8, self.height()), 8, 8)
            painter.setPen(QPen(QColor(ring), 2))
            painter.setBrush(QColor(fill))
            painter.drawEllipse(QPointF(cx, cy), radius, radius)
            if state == "done":
                painter.drawPixmap(QPointF(cx - 8, cy - 8), pixmap("check", "#FFFFFF", 16, 2.0, 3.0))
            else:
                painter.setPen(QColor(ink))
                painter.setFont(title_font)
                painter.drawText(QRectF(cx - radius, cy - radius, radius * 2, radius * 2), Qt.AlignCenter, str(index + 1))
            painter.setFont(title_font)
            painter.setPen(QColor(theme.TEXT if state != "todo" else theme.MUTED))
            painter.drawText(QRectF(slot * index + 6, 38, slot - 12, 18), Qt.AlignHCenter | Qt.AlignVCenter, step["title"])
            painter.setFont(caption_font)
            painter.setPen(QColor({"attention": theme.WARN, "done": theme.GOOD}.get(state, theme.MUTED)))
            caption = QFontMetrics(caption_font).elidedText(step.get("caption", ""), Qt.ElideRight, int(slot - 12))
            painter.drawText(QRectF(slot * index + 6, 57, slot - 12, 18), Qt.AlignHCenter | Qt.AlignVCenter, caption)


# ------------------------------------------------------------------ delegates
class TintDelegate(QStyledItemDelegate):
    """Keeps per-cell background tints visible; a stylesheet on the table would otherwise hide them."""

    def paint(self, painter, option, index):
        view = option.widget
        grid = view is not None and bool(view.property("cellGrid"))
        current = grid and view.currentIndex().isValid() and view.currentIndex() == index
        if grid and not current and view.currentIndex().isValid() and view.currentIndex().row() == index.row():
            painter.fillRect(option.rect.adjusted(0, 0, -1, -1), QColor(theme.ROW_ACTIVE))   # the row being worked on
        brush = index.data(Qt.BackgroundRole)
        if isinstance(brush, QBrush) and brush.style() != Qt.NoBrush:
            painter.fillRect(option.rect.adjusted(0, 0, -1, -1), brush)
        super().paint(painter, option, index)
        if current:                                            # unmistakable cursor: thick ring around the active cell
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing, False)
            painter.setPen(QPen(QColor(theme.ACCENT), 3))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(option.rect.adjusted(1, 1, -2, -2))
            painter.restore()


def grid_table(widget: QTableWidget) -> QTableWidget:
    """Spreadsheet look for tables people type into: visible cell borders, cell cursor, active row band."""
    widget.setProperty("cellGrid", True)
    widget.setObjectName("gridTable")
    widget.setShowGrid(True)
    widget.setSelectionBehavior(QAbstractItemView.SelectItems)
    widget.setSelectionMode(QAbstractItemView.SingleSelection)
    widget.currentCellChanged.connect(lambda *_a: widget.viewport().update())
    return widget


class PillDelegate(QStyledItemDelegate):
    """Paints a table cell's text as a status pill; ``tone_for`` maps the text to a tone."""

    def __init__(self, tone_for: Callable[[str], str], parent=None):
        super().__init__(parent)
        self.tone_for = tone_for

    def paint(self, painter, option, index):
        text = str(index.data(Qt.DisplayRole) or "")
        style = option.widget.style() if option.widget else None
        if style is not None:
            style.drawPrimitive(QStyle.PE_PanelItemViewItem, option, painter, option.widget)
        if not text:
            return
        fg, bg, border = theme.TONES[self.tone_for(text)]
        font = QFont(option.font)
        font.setPixelSize(12)
        font.setWeight(QFont.DemiBold)
        metrics = QFontMetrics(font)
        width = min(option.rect.width() - 12, metrics.horizontalAdvance(text) + 18)
        rect = QRectF(option.rect.left() + 8, option.rect.center().y() - 10, max(20, width), 21)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(border), 1))
        painter.setBrush(QColor(bg))
        painter.drawRoundedRect(rect, 10, 10)
        painter.setPen(QColor(fg))
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignCenter, metrics.elidedText(text, Qt.ElideRight, int(rect.width() - 10)))
        painter.restore()

    def sizeHint(self, option, index):  # noqa: N802
        hint = super().sizeHint(option, index)
        return QSize(hint.width() + 22, max(hint.height(), 30))


QUEUE_TITLE, QUEUE_SUB, QUEUE_TONE, QUEUE_BADGE = (Qt.UserRole + offset for offset in range(1, 5))


class QueueDelegate(QStyledItemDelegate):
    """Two-line review-queue row: status dot, sheet identity, issue summary, and a badge."""

    def sizeHint(self, option, index):  # noqa: N802
        return QSize(200, 56)

    def paint(self, painter, option, index):
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(option.rect)
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)
        if selected:
            painter.fillRect(rect, QColor(theme.ACCENT_SOFT))
            painter.fillRect(QRectF(rect.left(), rect.top(), 3, rect.height()), QColor(theme.ACCENT))
        elif hovered:
            painter.fillRect(rect, QColor(theme.SUBTLE))
        tone = index.data(QUEUE_TONE) or "neutral"
        dot = {"good": theme.GOOD, "warn": "#D97706", "bad": theme.BAD}.get(tone, theme.FAINT)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(dot))
        painter.drawEllipse(QPointF(rect.left() + 18, rect.top() + 21), 4.5, 4.5)
        badge = str(index.data(QUEUE_BADGE) or "")
        title_font = QFont(option.font)
        title_font.setPixelSize(14)
        title_font.setWeight(QFont.DemiBold)
        sub_font = QFont(option.font)
        sub_font.setPixelSize(12)
        badge_width = 0
        if badge:
            metrics = QFontMetrics(sub_font)
            badge_width = metrics.horizontalAdvance(badge) + 16
            badge_rect = QRectF(rect.right() - badge_width - 10, rect.top() + 11, badge_width, 20)
            fg, bg, border = theme.TONES[tone if tone in theme.TONES else "neutral"]
            painter.setPen(QPen(QColor(border), 1))
            painter.setBrush(QColor(bg))
            painter.drawRoundedRect(badge_rect, 10, 10)
            painter.setPen(QColor(fg))
            painter.setFont(sub_font)
            painter.drawText(badge_rect, Qt.AlignCenter, badge)
        text_left = rect.left() + 32
        text_width = rect.width() - 44 - badge_width
        painter.setFont(title_font)
        painter.setPen(QColor(theme.TEXT))
        title = QFontMetrics(title_font).elidedText(str(index.data(QUEUE_TITLE) or ""), Qt.ElideRight, int(text_width))
        painter.drawText(QRectF(text_left, rect.top() + 9, text_width, 20), Qt.AlignLeft | Qt.AlignVCenter, title)
        painter.setFont(sub_font)
        painter.setPen(QColor(theme.MUTED))
        sub = QFontMetrics(sub_font).elidedText(str(index.data(QUEUE_SUB) or ""), Qt.ElideRight, int(rect.width() - 44))
        painter.drawText(QRectF(text_left, rect.top() + 30, rect.width() - 44, 18), Qt.AlignLeft | Qt.AlignVCenter, sub)
        painter.restore()


# ------------------------------------------------------------------ score distribution
class Histogram(QWidget):
    """Single-series score distribution. One hue, thin rounded bars, recessive axis, hover readout."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._bins: list[tuple[float, float, int]] = []   # (low, high, count)
        self._mean: float | None = None
        self._hover = -1
        self.setMinimumHeight(215)
        self.setMouseTracking(True)
        self.setAccessibleName("Score distribution")

    def set_scores(self, scores: list[float], maximum: float) -> None:
        self._bins, self._mean, self._hover = [], None, -1
        if scores:
            low = min(0.0, min(scores))
            high = max(maximum, max(scores), low + 1)
            count = 10
            width = (high - low) / count
            counts = [0] * count
            for score in scores:
                counts[min(count - 1, int((score - low) / width))] += 1
            self._bins = [(low + i * width, low + (i + 1) * width, counts[i]) for i in range(count)]
            self._mean = sum(scores) / len(scores)
        self.setAccessibleDescription("; ".join(f"{a:g} to {b:g}: {n} students" for a, b, n in self._bins) or "No scores yet")
        self.update()

    def bins(self) -> list[tuple[float, float, int]]:
        return self._bins

    def _plot(self) -> QRectF:
        return QRectF(34, 26, self.width() - 46, self.height() - 54)

    def _bar_rect(self, index: int) -> QRectF:
        plot = self._plot()
        slot = plot.width() / len(self._bins)
        peak = max(1, max(count for _a, _b, count in self._bins))
        height = plot.height() * self._bins[index][2] / peak
        return QRectF(plot.left() + slot * index + 1, plot.bottom() - height, slot - 2, height)

    def mouseMoveEvent(self, event):  # noqa: N802
        if not self._bins:
            return
        plot = self._plot()
        x = event.position().x()
        index = int((x - plot.left()) / (plot.width() / len(self._bins))) if plot.left() <= x <= plot.right() else -1
        index = index if 0 <= index < len(self._bins) else -1
        if index != self._hover:
            self._hover = index
            self.update()
        if index >= 0:
            low, high, count = self._bins[index]
            QToolTip.showText(event.globalPosition().toPoint(),
                              f"{count} student{'s' if count != 1 else ''} scored {low:g} to {high:g}", self)
        else:
            QToolTip.hideText()

    def leaveEvent(self, event):  # noqa: N802
        self._hover = -1
        self.update()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        small = QFont(self.font())
        small.setPixelSize(11)
        painter.setFont(small)
        plot = self._plot()
        if not self._bins:
            painter.setPen(QColor(theme.MUTED))
            painter.drawText(self.rect(), Qt.AlignCenter, "Scores appear here after a batch is scanned.")
            return
        peak = max(1, max(count for _a, _b, count in self._bins))
        for fraction in (0.0, 0.5, 1.0):
            y = plot.bottom() - plot.height() * fraction
            painter.setPen(QPen(QColor("#E9E5DA" if fraction else theme.BORDER_STRONG), 1))
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            painter.setPen(QColor(theme.MUTED))
            value = peak * fraction
            if fraction == 0 or value == int(value):
                painter.drawText(QRectF(0, y - 8, plot.left() - 6, 16), Qt.AlignRight | Qt.AlignVCenter, f"{int(value)}")
        for index, (low, high, count) in enumerate(self._bins):
            rect = self._bar_rect(index)
            if count:
                path = QPainterPath()
                radius = min(4.0, rect.width() / 2, rect.height())
                path.moveTo(rect.left(), rect.bottom())
                path.lineTo(rect.left(), rect.top() + radius)
                path.quadTo(rect.left(), rect.top(), rect.left() + radius, rect.top())
                path.lineTo(rect.right() - radius, rect.top())
                path.quadTo(rect.right(), rect.top(), rect.right(), rect.top() + radius)
                path.lineTo(rect.right(), rect.bottom())
                path.closeSubpath()
                painter.fillPath(path, QColor(theme.ACCENT_DARK if index == self._hover else theme.ACCENT))
            if index % 2 == 0:
                painter.setPen(QColor(theme.MUTED))
                painter.drawText(QRectF(rect.left() - 20, plot.bottom() + 5, 40, 14), Qt.AlignHCenter, f"{low:g}")
        painter.setPen(QColor(theme.MUTED))
        painter.drawText(QRectF(plot.right() - 30, plot.bottom() + 5, 40, 14), Qt.AlignHCenter, f"{self._bins[-1][1]:g}")
        if self._mean is not None:
            low, high = self._bins[0][0], self._bins[-1][1]
            x = plot.left() + plot.width() * (self._mean - low) / (high - low)
            pen = QPen(QColor(theme.TEXT_2), 1.5)
            pen.setStyle(Qt.DashLine)
            painter.setPen(pen)
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            text = f"avg {self._mean:.1f}"
            width = QFontMetrics(small).horizontalAdvance(text) + 8
            left = x + 4 if x + 4 + width < plot.right() else x - 4 - width
            painter.setPen(QColor(theme.TEXT_2))
            left = min(max(plot.left(), x - width / 2), plot.right() - width)
            painter.drawText(QRectF(left, plot.top() - 20, width, 14), Qt.AlignCenter, text)


# ------------------------------------------------------------------ scanned sheet viewer
class SheetViewer(QGraphicsView):
    """Zoomable, pannable view of a scanned sheet with read-mark overlays.

    Marks are drawn on top of the image so the operator can compare what the reader
    decided with what is actually on paper, and a focus region can be zoomed into.
    """

    zoomChanged = Signal(float)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("sheetViewer")
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setFrameShape(QFrame.NoFrame)
        # Fit mode fills the viewport width; horizontal travel appears only after
        # zooming in, so pan gestures and the horizontal bar stay usable.
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.setBackgroundBrush(QBrush(QColor("#E3DED1")))
        self.setMinimumSize(280, 240)
        self._pixmap_item = None
        self._overlay_items: list = []
        self._highlight_item = None
        self._fit_mode = True
        self._message = "Select a sheet to see its scan here."
        self.setAccessibleName("Scanned sheet image")

    # -- content
    def clear(self, message: str = "Select a sheet to see its scan here.") -> None:
        self._scene.clear()
        self._pixmap_item, self._overlay_items, self._highlight_item = None, [], None
        self._message = message
        self._scene.setSceneRect(QRectF())
        self.viewport().update()

    def has_image(self) -> bool:
        return self._pixmap_item is not None

    def set_image(self, image: QPixmap) -> None:
        self._scene.clear()
        self._overlay_items, self._highlight_item = [], None
        self._pixmap_item = self._scene.addPixmap(image)
        self._pixmap_item.setTransformationMode(Qt.SmoothTransformation)
        self._scene.setSceneRect(QRectF(image.rect()))
        self._fit_mode = True
        self.fit()

    def set_marks(self, marks: Iterable[tuple[float, float, float, str]], visible: bool = True) -> None:
        """Rings around bubbles: (centre x, centre y, radius, tone) in image pixels."""
        for item in self._overlay_items:
            self._scene.removeItem(item)
        self._overlay_items = []
        for x, y, radius, tone in marks:
            colour = {"good": "#16A34A", "bad": "#DC2626", "warn": "#D97706", "info": "#2563EB"}.get(tone, "#2563EB")
            pen = QPen(QColor(colour), max(2.0, radius * 0.22))
            item = self._scene.addEllipse(QRectF(x - radius, y - radius, radius * 2, radius * 2), pen)
            item.setVisible(visible)
            self._overlay_items.append(item)

    def set_marks_visible(self, visible: bool) -> None:
        for item in self._overlay_items:
            item.setVisible(visible)

    def highlight(self, rect: QRectF | None, zoom: bool = True) -> None:
        """Outline a region (for example one question row) and optionally zoom to it."""
        if self._highlight_item is not None:
            self._scene.removeItem(self._highlight_item)
            self._highlight_item = None
        if rect is None or self._pixmap_item is None:
            return
        pen = QPen(QColor(theme.ACCENT), 4)
        fill = QColor(theme.ACCENT)
        fill.setAlpha(26)
        self._highlight_item = self._scene.addRect(rect, pen, QBrush(fill))
        self._highlight_item.setZValue(1)
        if zoom:
            self._fit_mode = False
            margin_x, margin_y = rect.width() * 0.45, max(rect.height() * 2.2, 120)
            self.fitInView(rect.adjusted(-margin_x, -margin_y, margin_x, margin_y), Qt.KeepAspectRatio)
            self.zoomChanged.emit(self.zoom())

    # -- zoom
    def zoom(self) -> float:
        return self.transform().m11()

    def fit(self) -> None:
        if self._pixmap_item is None:
            return
        self._fit_mode = True
        self._fit_width()
        self.zoomChanged.emit(self.zoom())

    def _fit_width(self) -> None:
        """Scale to the available width and let the operator scroll down the full sheet."""
        if self._pixmap_item is None or self.viewport().width() <= 0:
            return
        width = self._pixmap_item.boundingRect().width()
        if width <= 0:
            return
        self.resetTransform()
        scale = max(0.01, (self.viewport().width() - 12) / width)
        self.scale(scale, scale)
        self.horizontalScrollBar().setValue(0)

    def zoom_by(self, factor: float) -> None:
        if self._pixmap_item is None:
            return
        target = min(6.0, max(0.05, self.zoom() * factor))
        self._fit_mode = False
        self.scale(target / self.zoom(), target / self.zoom())
        self.zoomChanged.emit(self.zoom())

    def actual_size(self) -> None:
        if self._pixmap_item is None:
            return
        self._fit_mode = False
        self.resetTransform()
        self.zoomChanged.emit(self.zoom())

    # -- events
    def wheelEvent(self, event):  # noqa: N802
        if event.modifiers() & Qt.ControlModifier:
            self.zoom_by(1.15 if event.angleDelta().y() > 0 else 1 / 1.15)
            event.accept()
        else:
            super().wheelEvent(event)

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        if self._fit_mode:
            self.zoom_by(2.5)
        else:
            self.fit()

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() in (Qt.Key_Plus, Qt.Key_Equal):
            self.zoom_by(1.2)
        elif event.key() == Qt.Key_Minus:
            self.zoom_by(1 / 1.2)
        elif event.key() == Qt.Key_0:
            self.fit()
        else:
            super().keyPressEvent(event)

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        if self._fit_mode and self._pixmap_item is not None:
            self._fit_width()

    def drawForeground(self, painter, rect):  # noqa: N802
        if self._pixmap_item is None and self._message:
            painter.save()
            painter.resetTransform()
            painter.setPen(QColor(theme.MUTED))
            painter.drawText(self.viewport().rect().adjusted(24, 24, -24, -24), Qt.AlignCenter | Qt.TextWordWrap, self._message)
            painter.restore()


# ------------------------------------------------------------------ suggestion field and cell editor
class SuggestField(QWidget):
    """A text box with the most used values shown as one-click chips (replaces an editable drop-down)."""

    textChanged = Signal(str)

    def __init__(self, suggestions: Iterable[str] = (), placeholder: str = "", text: str = "", limit: int = 5,
                 parent: QWidget | None = None):
        super().__init__(parent)
        from PySide6.QtWidgets import QLineEdit

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(5)
        self.edit = QLineEdit(text)
        self.edit.setPlaceholderText(placeholder)
        self.edit.textChanged.connect(self.textChanged)
        outer.addWidget(self.edit)
        values = sorted({s.strip() for s in suggestions if s and s.strip()}, key=str.casefold)[:limit]
        if values:
            row = QHBoxLayout()
            row.setSpacing(5)
            for value in values:
                chip = QPushButton(value)
                chip.setObjectName("chip")
                chip.setCursor(Qt.PointingHandCursor)
                chip.setToolTip(f"Use {value}")
                chip.clicked.connect(lambda _c=False, v=value: (self.edit.setText(v), self.edit.setFocus()))
                row.addWidget(chip)
            row.addStretch(1)
            outer.addLayout(row)

    def text(self) -> str:
        return self.edit.text()

    def setText(self, value: str) -> None:
        self.edit.setText(value)

    def setPlaceholderText(self, value: str) -> None:
        self.edit.setPlaceholderText(value)

    def setFocus(self, *args) -> None:  # noqa: N802
        self.edit.setFocus(*args)


class ChoiceDelegate(TintDelegate):
    """In-cell editor for a short fixed set of values: a plain box that only accepts those values.

    Typing replaces the cell's content, so a whole answer key can be entered from the keyboard
    without any pop-up list getting in the way.
    """

    def __init__(self, values: Iterable[str], parent=None, *, blank_hint: str = ""):
        super().__init__(parent)
        self.values = [str(v).upper() for v in values]
        self.blank_hint = blank_hint

    def createEditor(self, parent, _option, _index):  # noqa: N802
        from PySide6.QtCore import QRegularExpression
        from PySide6.QtGui import QRegularExpressionValidator
        from PySide6.QtWidgets import QLineEdit

        editor = QLineEdit(parent)
        editor.setAlignment(Qt.AlignCenter)
        editor.setMaxLength(max((len(v) for v in self.values), default=1))
        pattern = "|".join(QRegularExpression.escape(v) for v in self.values)
        editor.setValidator(QRegularExpressionValidator(QRegularExpression(f"(?:{pattern})?", QRegularExpression.CaseInsensitiveOption), editor))
        editor.setObjectName("cellEditor")
        editor.textEdited.connect(lambda text, e=editor: self._typed(e, text))
        return editor

    def _typed(self, editor, text: str) -> None:
        editor.setText(text.upper())
        if text.upper() in self.values:         # a complete answer: save it and move on, no Enter needed
            self.commitData.emit(editor)
            self.closeEditor.emit(editor, QStyledItemDelegate.NoHint)

    def setEditorData(self, editor, index):  # noqa: N802
        editor.setText(str(index.data(Qt.EditRole) or "").strip().upper())
        editor.selectAll()

    def setModelData(self, editor, model, index):  # noqa: N802
        model.setData(index, editor.text().strip().upper(), Qt.EditRole)

    def updateEditorGeometry(self, editor, option, _index):  # noqa: N802
        editor.setGeometry(option.rect)


# ------------------------------------------------------------------ custom dropdown
def combo(min_width: int = 0):
    """Drop-down whose list is drawn by the app's own stylesheet instead of the OS popup."""
    from PySide6.QtWidgets import QComboBox, QListView

    widget = QComboBox()
    view = QListView()
    view.setObjectName("comboList")
    widget.setView(view)
    widget.setMaxVisibleItems(14)
    if min_width:
        widget.setMinimumWidth(min_width)
    return widget


# ------------------------------------------------------------------ custom dialogs
from PySide6.QtWidgets import QDialog, QPlainTextEdit  # noqa: E402


class AppDialog(QDialog):
    """Base for every dialog: app-styled header, body area, and a right-aligned button row."""

    def __init__(self, parent: QWidget | None, title: str, subtitle: str = "", icon_name: str = "", tone: str = "info",
                 width: int = 480):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowFlag(Qt.WindowContextHelpButtonHint, False)
        self.setMinimumWidth(width)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(22, 20, 22, 6)
        head.setSpacing(14)
        if icon_name:
            head.addWidget(icon_tile(icon_name, tone, 40), 0, Qt.AlignTop)
        titles = QVBoxLayout()
        titles.setSpacing(3)
        self.title_label = label(title, "cardTitle")
        self.subtitle_label = label(subtitle, "muted")
        self.subtitle_label.setTextFormat(Qt.RichText)
        self.subtitle_label.setVisible(bool(subtitle))
        self.subtitle_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        titles.addWidget(self.title_label)
        titles.addWidget(self.subtitle_label)
        head.addLayout(titles, 1)
        outer.addLayout(head)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(22, 10, 22, 6)
        self.body.setSpacing(10)
        outer.addLayout(self.body, 1)
        foot = QFrame()
        foot.setObjectName("dialogFoot")
        self.buttons = QHBoxLayout(foot)
        self.buttons.setContentsMargins(22, 12, 22, 14)
        self.buttons.setSpacing(10)
        self.buttons.addStretch(1)
        outer.addWidget(foot)

    def add_button(self, text: str, kind: str = "", accept: bool | None = None, handler: Callable[[], Any] | None = None,
                   default: bool = False) -> QPushButton:
        widget = button(text, handler, kind)
        widget.setAutoDefault(default)
        widget.setDefault(default)
        if accept is True:
            widget.clicked.connect(self.accept)
        elif accept is False:
            widget.clicked.connect(self.reject)
        self.buttons.addWidget(widget)
        return widget


_MESSAGE_ICONS = {"info": "info", "good": "check-circle", "warn": "alert", "bad": "x-circle"}


def alert(parent: QWidget | None, title: str, message: str, tone: str = "info", button_text: str = "OK") -> None:
    """Custom replacement for information/warning/error message boxes."""
    dialog = AppDialog(parent, title, message.replace("\n", "<br>"), _MESSAGE_ICONS.get(tone, "info"), tone)
    dialog.add_button(button_text, "primary", accept=True, default=True)
    dialog.exec()


def confirm(parent: QWidget | None, title: str, message: str, confirm_text: str = "Continue",
            cancel_text: str = "Cancel", danger: bool = False) -> bool:
    """Custom yes/no prompt. The safe choice is the default for destructive actions."""
    dialog = AppDialog(parent, title, message.replace("\n", "<br>"), "alert" if danger else "info",
                       "warn" if danger else "info")
    dialog.add_button(cancel_text, "", accept=False, default=danger)
    dialog.add_button(confirm_text, "danger" if danger else "primary", accept=True, default=not danger)
    return dialog.exec() == QDialog.Accepted


def ask_text(parent: QWidget | None, title: str, message: str, placeholder: str = "",
             confirm_text: str = "Apply") -> str | None:
    """Custom multi-line text prompt; returns None when cancelled."""
    dialog = AppDialog(parent, title, message, "edit", "info", width=520)
    editor = QPlainTextEdit()
    editor.setPlaceholderText(placeholder)
    editor.setMinimumHeight(220)
    dialog.body.addWidget(editor)
    dialog.add_button("Cancel", "", accept=False)
    dialog.add_button(confirm_text, "primary", accept=True, default=True)
    editor.setFocus()
    return editor.toPlainText() if dialog.exec() == QDialog.Accepted else None


class BusyDialog(AppDialog):
    """Small wait window for background work that cannot be interrupted."""

    def __init__(self, parent: QWidget | None, message: str):
        super().__init__(parent, message, "This usually takes a few seconds.", "clock", "info", width=400)
        from PySide6.QtWidgets import QProgressBar

        self.setWindowModality(Qt.WindowModal)
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)
        bar = QProgressBar()
        bar.setRange(0, 0)
        bar.setTextVisible(False)
        self.body.addWidget(bar)
        self._done = False

    def finish(self) -> None:
        self._done = True
        self.close()

    def reject(self) -> None:  # Esc must not dismiss work in progress
        if self._done:
            super().reject()

    def closeEvent(self, event):  # noqa: N802
        event.accept() if self._done else event.ignore()
