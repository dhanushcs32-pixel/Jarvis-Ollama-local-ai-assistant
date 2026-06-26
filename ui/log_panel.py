"""
ui/log_panel.py — Transparent frosted glass panel with edge refraction.
"""

from __future__ import annotations
import random

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QScrollArea, QLabel,
    QHBoxLayout, QSizePolicy, QFrame,
)
from PyQt6.QtCore  import Qt, QTimer, QPropertyAnimation, QEasingCurve, QRectF, QPointF
from PyQt6.QtGui   import (
    QPainter, QColor, QPainterPath, QPen,
    QPixmap, QImage, QBrush, QRadialGradient, QConicalGradient, QLinearGradient,
)

PANEL_W = 340
PANEL_H = 420
MARGIN  = 20
RADIUS  = 18

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
            alpha = rng.randint(0, 55)
            img.setPixelColor(x, y, QColor(gray, gray, gray, alpha))
    _NOISE_PIXMAP = QPixmap.fromImage(img)
    return _NOISE_PIXMAP


class LogPanel(QWidget):

    def __init__(self, screen_rect) -> None:
        super().__init__()
        self._screen     = screen_rect
        self._in_anim:   QPropertyAnimation | None = None
        self._hide_anim: QPropertyAnimation | None = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAutoFillBackground(False)

        # Belt AND suspenders — every size hint method returns the same value
        self.setFixedSize(PANEL_W, PANEL_H)

        self._position()
        self._build_ui()
        self._animate_in()
        self.show()

    # ── Paint ─────────────────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:                   # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, w, h), RADIUS, RADIUS)

        # 1. Base glass fill
        painter.fillPath(path, QColor(255, 255, 255, 28))

        # 2. Tiled noise grain
        painter.setClipPath(path)
        painter.drawTiledPixmap(self.rect(), _get_noise_pixmap())

        # 3. Inner vignette
        vign = QRadialGradient(w / 2, h / 2, max(w, h) * 0.75)
        vign.setColorAt(0.0, QColor(255, 255, 255,  0))
        vign.setColorAt(1.0, QColor(0,   0,   0,   30))
        painter.setBrush(QBrush(vign))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(path)

        # 4. Prismatic edge refraction
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

        catch = QLinearGradient(0, 0, w * 0.45, h * 0.25)
        catch.setColorAt(0.0, QColor(255, 255, 255, 110))
        catch.setColorAt(0.5, QColor(255, 255, 255,  25))
        catch.setColorAt(1.0, QColor(255, 255, 255,   0))
        cp = QPainterPath()
        cp.addRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), RADIUS, RADIUS)
        painter.setPen(QPen(QBrush(catch), 1.0))
        painter.drawPath(cp)

        painter.end()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _position(self) -> None:
        x = self._screen.right()  - PANEL_W - MARGIN
        y = self._screen.bottom() - PANEL_H - MARGIN
        self.move(x, y)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header (fixed 44px) ───────────────────────────────────────────
        header = QWidget(self)
        header.setFixedHeight(44)
        header.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        header.setAutoFillBackground(False)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(16, 0, 16, 0)
        title = QLabel("● Vision Assistant")
        title.setStyleSheet(
            "color: rgba(255,255,255,200); font-size: 12px;"
            "font-family: 'SF Pro Text', 'Segoe UI', 'Helvetica Neue', sans-serif;"
            "font-weight: 600; letter-spacing: 0.5px; background: transparent;"
        )
        hl.addWidget(title)
        hl.addStretch()

        # ── Separator ─────────────────────────────────────────────────────
        sep = QWidget(self)
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: rgba(255,255,255,18);")

        # ── Scroll area ───────────────────────────────────────────────────
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)

        # Force every layer of the scroll area transparent
        self._scroll.setAutoFillBackground(False)
        self._scroll.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        vp = self._scroll.viewport()
        vp.setAutoFillBackground(False)
        vp.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._scroll.setStyleSheet("""
            QScrollArea,
            QScrollArea > QWidget,
            QScrollArea > QWidget > QWidget {
                background: transparent;
                border: none;
            }
            QScrollBar:vertical  { background: transparent; width: 3px; margin: 4px 2px; }
            QScrollBar::handle:vertical {
                background: rgba(255,255,255,55);
                border-radius: 1px; min-height: 20px;
            }
            QScrollBar::add-line:vertical,
            QScrollBar::sub-line:vertical { height: 0px; }
        """)

        # Content widget inside scroll
        self._content = QWidget()
        self._content.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._content.setAutoFillBackground(False)
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(14, 10, 14, 10)
        self._content_layout.setSpacing(6)
        self._content_layout.addStretch()
        self._scroll.setWidget(self._content)

        # stretch=1 → scroll area takes ALL space the header doesn't use
        root.addWidget(header)
        root.addWidget(sep)
        root.addWidget(self._scroll, stretch=1)

        # Final insurance: after layout is built, force scroll area to fill
        # remaining height explicitly (header=44, sep=1, rest=scroll)
        QTimer.singleShot(0, self._enforce_size)

    def _enforce_size(self) -> None:
        """
        Called once after the event loop starts.
        Re-asserts the fixed size so the window manager can't have
        shrunk us during show(). Also fixes the scroll area height
        in case the layout engine got confused.
        """
        self.setFixedSize(PANEL_W, PANEL_H)
        scroll_h = PANEL_H - 44 - 1   # total - header - sep
        self._scroll.setMinimumHeight(scroll_h)

    # ── Animations ────────────────────────────────────────────────────────────

    def _animate_in(self) -> None:
        anim = QPropertyAnimation(self, b"windowOpacity")
        anim.setDuration(400)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(lambda: setattr(self, "_in_anim", None))
        self._in_anim = anim
        anim.start()

    # ── Public API ────────────────────────────────────────────────────────────

    def add_message(self, text: str, role: str = "assistant") -> None:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        label.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        label.setAutoFillBackground(False)

        if role == "user":
            color = "rgba(160,230,255,255)"
        else:
            color = "white"

        label.setStyleSheet(f"""
            color: {color};
            font-size: 13px;
            font-family: 'SF Pro Text', 'Segoe UI', 'Helvetica Neue', sans-serif;
            background: transparent;
            padding: 3px 2px;
        """)

        # Drop shadow via QGraphicsDropShadowEffect — works on any background
        from PyQt6.QtWidgets import QGraphicsDropShadowEffect
        shadow = QGraphicsDropShadowEffect(label)
        shadow.setBlurRadius(6)
        shadow.setOffset(1, 1)
        shadow.setColor(QColor(0, 0, 0, 200))
        label.setGraphicsEffect(shadow)

        count = self._content_layout.count()
        self._content_layout.insertWidget(count - 1, label)

        QTimer.singleShot(
            50,
            lambda: self._scroll.verticalScrollBar().setValue(
                self._scroll.verticalScrollBar().maximum()
            ),
        )

    def add_user_query(self, text: str) -> None:
        self.add_message(f"You: {text}", role="user")

    def add_ai_response(self, text: str) -> None:
        self.add_message(text, role="assistant")

    def hide_for_overlay(self) -> None:
        if not self.isVisible():
            return
        if self._in_anim is not None:
            self._in_anim.stop()
            self._in_anim = None
        anim = QPropertyAnimation(self, b"windowOpacity")
        anim.setDuration(200)
        anim.setStartValue(self.windowOpacity())
        anim.setEndValue(0.0)
        anim.finished.connect(self.hide)
        anim.finished.connect(lambda: setattr(self, "_hide_anim", None))
        self._hide_anim = anim
        anim.start()

    def show_after_overlay(self) -> None:
        self.show()
        self._animate_in()
