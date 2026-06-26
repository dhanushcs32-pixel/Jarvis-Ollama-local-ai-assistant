"""
ui/text_input.py — Floating command input bar.

Press F10 to show/hide. Type a command and press Enter to send it
through the same handler as voice commands. Esc hides it.
"""

import random

from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLineEdit, QLabel
from PyQt6.QtCore    import Qt, QPropertyAnimation, QEasingCurve, pyqtSignal, QRectF, QPointF
from PyQt6.QtGui     import (
    QPainter, QColor, QPainterPath, QKeyEvent,
    QPixmap, QImage, QBrush, QPen,
    QRadialGradient, QConicalGradient, QLinearGradient,
)

_NOISE_PIXMAP: QPixmap | None = None

def _get_noise_pixmap() -> QPixmap:
    global _NOISE_PIXMAP
    if _NOISE_PIXMAP is not None:
        return _NOISE_PIXMAP
    size = 128
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    rng = random.Random(42)
    for y in range(size):
        for x in range(size):
            gray  = rng.randint(0, 255)
            alpha = rng.randint(0, 40)          # subtler grain for a slim bar
            img.setPixelColor(x, y, QColor(gray, gray, gray, alpha))
    _NOISE_PIXMAP = QPixmap.fromImage(img)
    return _NOISE_PIXMAP


class TextInputBar(QWidget):
    """
    A slim frosted-glass input bar that floats at the bottom-centre of the screen.
    Emits ``command_submitted(str)`` when the user presses Enter.
    """

    command_submitted = pyqtSignal(str)

    def __init__(self, screen_rect) -> None:
        super().__init__()
        self._screen = screen_rect

        # Strong references to in-flight animations (prevent GC mid-run).
        self._in_anim:  QPropertyAnimation | None = None
        self._out_anim: QPropertyAnimation | None = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        bar_w, bar_h = 600, 52
        x = screen_rect.x() + (screen_rect.width()  - bar_w) // 2
        y = screen_rect.y() +  screen_rect.height()          - 120
        self.setGeometry(x, y, bar_w, bar_h)

        self._build_ui()
        self.hide()

    # ── Layout ────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(10)

        icon = QLabel("⌨")
        icon.setStyleSheet(
            "color: rgba(255,255,255,220); font-size: 16px; background: transparent;"
        )
        icon.setFixedWidth(22)
        layout.addWidget(icon)

        self._field = QLineEdit()
        self._field.setPlaceholderText("Type a command and press Enter…")
        self._field.setStyleSheet("""
            QLineEdit {
                background: transparent;
                border: none;
                color: rgba(255, 255, 255, 255);
                font-family: 'SF Pro Text', 'Segoe UI', 'Helvetica Neue', sans-serif;
                font-size: 14px;
                selection-background-color: rgba(60, 160, 255, 120);
            }
            QLineEdit::placeholder {
                color: rgba(255, 255, 255, 120);
            }
        """)
        self._field.returnPressed.connect(self._submit)
        layout.addWidget(self._field)

        hint = QLabel("Esc to close")
        hint.setStyleSheet(
            "color: rgba(255,255,255,100); font-size: 11px;"
            "font-family: 'SF Pro Text', 'Segoe UI', 'Helvetica Neue', sans-serif; background: transparent;"
        )
        layout.addWidget(hint)

    # ── Paint ─────────────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h   = self.width(), self.height()
        RADIUS = 14

        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, w, h), RADIUS, RADIUS)

        # 1. Base glass fill — matches LogPanel transparency
        painter.fillPath(path, QColor(255, 255, 255, 28))

        # 2. Tiled noise grain
        painter.setClipPath(path)
        painter.drawTiledPixmap(self.rect(), _get_noise_pixmap())

        # 3. Inner vignette — subtle darkening at edges
        vign = QRadialGradient(w / 2, h / 2, max(w, h) * 0.75)
        vign.setColorAt(0.0, QColor(255, 255, 255,  0))
        vign.setColorAt(1.0, QColor(0,   0,   0,   25))
        painter.setBrush(QBrush(vign))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(path)

        # 4. Prismatic edge refraction (same palette as LogPanel)
        painter.setClipping(False)
        cx, cy = w / 2.0, h / 2.0

        def cone(angle: int, alpha: int) -> QConicalGradient:
            g = QConicalGradient(QPointF(cx, cy), angle)
            g.setColorAt(0.00, QColor(255,  80,  80, alpha))
            g.setColorAt(0.18, QColor(255, 200,  60, alpha))
            g.setColorAt(0.35, QColor( 80, 255, 160, alpha))
            g.setColorAt(0.52, QColor( 60, 160, 255, alpha))
            g.setColorAt(0.70, QColor(180,  80, 255, alpha))
            g.setColorAt(0.85, QColor(255,  80, 180, alpha))
            g.setColorAt(1.00, QColor(255,  80,  80, alpha))
            return g

        outer_path = QPainterPath()
        outer_path.addRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), RADIUS, RADIUS)
        painter.setPen(QPen(QBrush(cone(90, 75)), 3.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(outer_path)

        inner_path = QPainterPath()
        inner_path.addRoundedRect(QRectF(2, 2, w - 4, h - 4), RADIUS - 2, RADIUS - 2)
        painter.setPen(QPen(QBrush(cone(110, 140)), 1.2))
        painter.drawPath(inner_path)

        # 5. Top-left specular catch-light
        catch = QLinearGradient(0, 0, w * 0.55, h * 0.6)
        catch.setColorAt(0.0, QColor(255, 255, 255, 90))
        catch.setColorAt(0.5, QColor(255, 255, 255, 18))
        catch.setColorAt(1.0, QColor(255, 255, 255,  0))
        cp = QPainterPath()
        cp.addRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), RADIUS, RADIUS)
        painter.setPen(QPen(QBrush(catch), 1.0))
        painter.drawPath(cp)

        painter.end()

    # ── Show / hide with fade ─────────────────────────────────────────────

    def toggle(self) -> None:
        if self.isVisible():
            self._hide_animated()
        else:
            self._show_animated()

    def _show_animated(self) -> None:
        # Cancel any in-progress hide.
        if self._out_anim is not None:
            self._out_anim.stop()
            self._out_anim = None

        self.setWindowOpacity(0.0)
        self.show()
        self._field.clear()
        self._field.setFocus()

        anim = QPropertyAnimation(self, b"windowOpacity")
        anim.setDuration(180)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(lambda: setattr(self, "_in_anim", None))
        self._in_anim = anim
        anim.start()

    def _hide_animated(self) -> None:
        # Cancel any in-progress show.
        if self._in_anim is not None:
            self._in_anim.stop()
            self._in_anim = None

        anim = QPropertyAnimation(self, b"windowOpacity")
        anim.setDuration(150)
        anim.setStartValue(self.windowOpacity())
        anim.setEndValue(0.0)
        anim.finished.connect(self.hide)
        anim.finished.connect(lambda: setattr(self, "_out_anim", None))
        self._out_anim = anim
        anim.start()

    # ── Submit ────────────────────────────────────────────────────────────

    def _submit(self) -> None:
        text = self._field.text().strip()
        if not text:
            return
        self._field.clear()
        self._hide_animated()

        # [FIXED - Session 36] Typed commands never pass through
        # voice/listener.py's wake-word extraction, so a habitually-typed
        # "jarvis"/"jarv" prefix would otherwise reach the agent verbatim
        # and corrupt prompts sent to Moondream (silent empty response —
        # see vision_assistant_state.txt Session 36 for the original bug).
        try:
            from voice.listener import strip_wake_word
            stripped = strip_wake_word(text)
            text = stripped if stripped else "hello"
        except Exception:
            pass  # listener module not importable — fall back to raw text

        self.command_submitted.emit(text)

    # ── Keyboard ──────────────────────────────────────────────────────────

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self._hide_animated()
        else:
            super().keyPressEvent(event)
