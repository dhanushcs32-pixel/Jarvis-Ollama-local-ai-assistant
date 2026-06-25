"""
ui/eye_widget.py — Always-on-top floating eye that follows the cursor.

Renders the blue ring logo from the asset file, with black made transparent.
Animates:
  - idle      : slow ring rotation
  - listening : fast pulse + glow
  - thinking  : spinning rings
  - speaking  : ripple outward
"""

import math
from pathlib import Path

from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore    import Qt, QTimer, QPoint
from PyQt6.QtGui     import (
    QPainter, QPixmap, QColor, QRadialGradient,
    QPen, QBrush, QCursor,
)

ASSET  = Path(__file__).parent.parent / "assets" / "eye.png"
SIZE   = 42                     # widget size in pixels
OFFSET = QPoint(14, 14)         # offset from cursor so it doesn't block clicks

_STATE_SPEED: dict[str, float] = {
    "idle":      0.4,
    "listening": 2.5,
    "thinking":  3.5,
    "speaking":  1.5,
}


class EyeWidget(QWidget):
    """Frameless, transparent, always-on-top eye widget."""

    IDLE      = "idle"
    LISTENING = "listening"
    THINKING  = "thinking"
    SPEAKING  = "speaking"

    def __init__(self) -> None:
        super().__init__()
        self._state:           str          = self.IDLE
        self._angle:           float        = 0.0
        self._pulse:           float        = 0.0
        self._tick:            int          = 0
        self._last_cursor_pos: QPoint | None = None

        # Try to load the eye asset once at startup.
        self._pixmap: QPixmap | None = None
        if ASSET.exists():
            px = QPixmap(str(ASSET))
            if not px.isNull():
                # Strip white background using colour mask
                mask = px.createMaskFromColor(
                    QColor(255, 255, 255),
                    Qt.MaskMode.MaskOutColor,
                )
                px.setMask(mask)
                self._pixmap = px.scaled(
                    SIZE, SIZE,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput   # clicks pass through
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(SIZE + 20, SIZE + 20)   # extra room for glow
        self.show()

        # Cursor tracking timer (~60 fps).
        self._track_timer = QTimer(self)
        self._track_timer.timeout.connect(self._follow_cursor)
        self._track_timer.start(16)

        # Animation timer (~30 fps).
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._animate)
        self._anim_timer.start(33)

    # ── Public API ────────────────────────────────────────────────────────

    def set_state(self, state: str) -> None:
        """Thread-safe via Qt signal; always called from the main thread."""
        if state not in _STATE_SPEED:
            return
        self._state = state
        self.update()

    # ── Cleanup ───────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:  # noqa: N802
        self._track_timer.stop()
        self._anim_timer.stop()
        super().closeEvent(event)

    # ── Internal ─────────────────────────────────────────────────────────

    def _follow_cursor(self) -> None:
        try:
            cp = QCursor.pos()
            if cp == self._last_cursor_pos:
                return   # cursor hasn't moved — skip redundant move()
            self._last_cursor_pos = cp
            self.move(cp.x() + OFFSET.x(), cp.y() + OFFSET.y())
        except Exception:
            pass   # stale handle on shutdown — ignore

    def _animate(self) -> None:
        self._tick += 1
        speed = _STATE_SPEED.get(self._state, 0.4)
        self._angle = (self._angle + speed) % 360
        self._pulse = (math.sin(self._tick * 0.15) + 1) / 2
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx = self.width()  / 2
        cy = self.height() / 2
        r  = SIZE / 2

        blue        = QColor(40,  140, 255)
        bright_blue = QColor(80,  180, 255)

        # Outer glow — only in active states.
        if self._state != self.IDLE:
            grad = QRadialGradient(cx, cy, r + 14)
            grad.setColorAt(0, QColor(40, 140, 255, int(self._pulse * 90)))
            grad.setColorAt(1, QColor(0,  0,   0,   0))
            painter.setBrush(QBrush(grad))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(
                int(cx - r - 14), int(cy - r - 14),
                int((r + 14) * 2), int((r + 14) * 2),
            )

        if self._pixmap is not None:
            painter.save()
            painter.translate(cx, cy)
            painter.rotate(self._angle * 0.3)
            painter.translate(-cx, -cy)
            offset = (self.width() - self._pixmap.width()) // 2
            painter.drawPixmap(offset, offset, self._pixmap)
            painter.restore()
        else:
            self._draw_rings(painter, cx, cy, r, blue, bright_blue)

        painter.end()

    def _draw_rings(
        self,
        painter:     QPainter,
        cx:          float,
        cy:          float,
        r:           float,
        blue:        QColor,
        bright_blue: QColor,
    ) -> None:
        """Fallback ring drawing when the asset is unavailable."""
        rings = [
            (r * 0.95, 3, 45),
            (r * 0.72, 4, 60),
            (r * 0.50, 3, 30),
        ]
        for i, (ring_r, width, gap_deg) in enumerate(rings):
            painter.save()
            painter.translate(cx, cy)
            direction = 1 if i % 2 == 0 else -1
            painter.rotate(self._angle * direction * (1 + i * 0.4))
            painter.translate(-cx, -cy)

            pen = QPen(bright_blue if i == 1 else blue, width)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)

            span = 360 - gap_deg
            painter.drawArc(
                int(cx - ring_r), int(cy - ring_r),
                int(ring_r * 2),  int(ring_r * 2),
                0, int(span * 16),
            )
            painter.restore()

        # Centre dot
        painter.setBrush(QBrush(bright_blue))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(int(cx - 4), int(cy - 4), 8, 8)
