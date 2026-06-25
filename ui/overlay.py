"""
ui/overlay.py — Fullscreen transparent overlay the AI draws on.

Two modes:
  - POINTER mode : clean Clicky-style pulsing circle + arrow, no side panel
  - FULL mode    : side panel + annotations for multi-step instructions
"""

import math

from PyQt6.QtWidgets import QWidget, QLabel, QVBoxLayout, QHBoxLayout
from PyQt6.QtCore    import Qt, QTimer, QPropertyAnimation, QEasingCurve, QPointF
from PyQt6.QtGui     import (
    QPainter, QColor, QPen, QBrush, QPainterPath,
    QFont, QLinearGradient, QRadialGradient, QPolygonF,
)

DISMISS_AFTER_MS = 10_000

BLUE  = QColor(60,  160, 255)
GREEN = QColor(80,  220, 140)
WHITE = QColor(255, 255, 255, 220)

ANNOTATION_COLORS: dict[str, QColor] = {
    "arrow":  QColor(60,  160, 255),
    "circle": QColor(80,  220, 140),
    "box":    QColor(255, 200,  60),
    "text":   QColor(255, 255, 255),
}


class InstructionsPanel(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedWidth(280)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), 20, 20)

        painter.fillPath(path, QColor(255, 255, 255, 6))

        grad = QLinearGradient(0, 0, 0, self.height())
        grad.setColorAt(0, QColor(255, 255, 255, 18))
        grad.setColorAt(1, QColor(255, 255, 255,  2))
        painter.setBrush(grad)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(path)

        painter.setPen(QPen(QColor(255, 255, 255, 20), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        painter.end()


class DrawingOverlay(QWidget):
    def __init__(self, screen_rect) -> None:
        super().__init__()
        self._screen        = screen_rect
        self._annotations:  list[dict] = []
        self._instructions: list[str]  = []
        self._draw_progress = 0.0
        self._tick          = 0
        self._pulse_tick    = 0
        self._pointer_mode  = False

        # Strong references to in-flight animations (prevent GC mid-run).
        self._in_anim:  QPropertyAnimation | None = None
        self._out_anim: QPropertyAnimation | None = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setGeometry(screen_rect)

        self._build_ui()

        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self.dismiss)

        self._draw_timer = QTimer(self)
        self._draw_timer.timeout.connect(self._tick_draw)

        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._tick_pulse)

    # ── Layout ────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(40, 60, 40, 60)

        self._instr_panel = InstructionsPanel(self)
        self._instr_layout = QVBoxLayout(self._instr_panel)
        self._instr_layout.setContentsMargins(20, 24, 20, 24)
        self._instr_layout.setSpacing(12)

        self._instr_title = QLabel("Instructions")
        self._instr_title.setStyleSheet(
            "color: rgba(255,255,255,200); font-size: 14px; font-weight: 700;"
            "font-family: 'Segoe UI', sans-serif; background: transparent;"
        )
        self._instr_layout.addWidget(self._instr_title)
        self._step_labels: list[QLabel] = []
        self._instr_layout.addStretch()

        layout.addWidget(self._instr_panel)
        layout.addStretch()

    # ── Public API ────────────────────────────────────────────────────────

    def show_annotations(self, data: dict) -> None:
        self._annotations   = data.get("annotations",  [])
        self._instructions  = data.get("instructions", [])
        self._draw_progress = 0.0
        self._tick          = 0
        self._pulse_tick    = 0

        self._pointer_mode = (
            len(self._annotations) == 1
            and self._annotations[0].get("type") == "circle"
            and not self._instructions
        ) or bool(data.get("pointer_mode", False))

        self._instr_panel.setVisible(not self._pointer_mode)

        if not self._pointer_mode:
            for lbl in self._step_labels:
                lbl.deleteLater()
            self._step_labels.clear()
            for i, step in enumerate(self._instructions):
                lbl = QLabel(f"{i + 1}.  {step}")
                lbl.setWordWrap(True)
                lbl.setStyleSheet(
                    "color: rgba(255,255,255,200); font-size: 13px;"
                    "font-family: 'Segoe UI', sans-serif;"
                    "background: transparent; padding: 6px 0px;"
                )
                self._instr_layout.insertWidget(
                    self._instr_layout.count() - 1, lbl
                )
                self._step_labels.append(lbl)

        self.show()
        self._animate_in()
        self._draw_timer.start(30)
        self._pulse_timer.start(40)
        self._dismiss_timer.start(DISMISS_AFTER_MS)

    def show_pointer(self, x: int, y: int, label: str) -> None:
        """Convenience: show a clean Clicky-style pointer at (x, y)."""
        self.show_annotations({
            "annotations": [
                {"type": "circle", "x": x, "y": y, "r": 48, "label": label},
                {
                    "type": "arrow",
                    "x1": self._screen.width()  // 2,
                    "y1": self._screen.height() // 2,
                    "x2": x, "y2": y,
                    "label": "",
                },
            ],
            "instructions": [],
            "pointer_mode": True,
        })

    def dismiss(self) -> None:
        """Fade out and hide. Guard against double-dismiss."""
        if not self.isVisible():
            return

        # Stop timers before the widget goes away.
        self._dismiss_timer.stop()
        self._draw_timer.stop()
        self._pulse_timer.stop()

        # Cancel any in-progress fade-in.
        if self._in_anim is not None:
            self._in_anim.stop()
            self._in_anim = None

        anim = QPropertyAnimation(self, b"windowOpacity")
        anim.setDuration(250)
        anim.setStartValue(self.windowOpacity())
        anim.setEndValue(0.0)
        anim.finished.connect(self.hide)
        anim.finished.connect(lambda: setattr(self, "_out_anim", None))
        self._out_anim = anim   # strong reference
        anim.start()

    # ── Qt events ────────────────────────────────────────────────────────

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Escape, Qt.Key.Key_F9):
            self.dismiss()
        else:
            super().keyPressEvent(event)

    # ── Animation ticks ───────────────────────────────────────────────────

    def _animate_in(self) -> None:
        anim = QPropertyAnimation(self, b"windowOpacity")
        anim.setDuration(300)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(lambda: setattr(self, "_in_anim", None))
        self._in_anim = anim
        anim.start()

    def _tick_draw(self) -> None:
        self._tick += 1
        self._draw_progress = min(1.0, self._tick / 18.0)
        self.update()
        if self._draw_progress >= 1.0:
            self._draw_timer.stop()

    def _tick_pulse(self) -> None:
        self._pulse_tick += 1
        self.update()

    # ── Paint ─────────────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if not self._pointer_mode:
            vignette = QRadialGradient(
                self.width() / 2, self.height() / 2,
                max(self.width(), self.height()) * 0.7,
            )
            vignette.setColorAt(0, QColor(0, 0, 0,  0))
            vignette.setColorAt(1, QColor(0, 0, 0, 55))
            painter.fillRect(self.rect(), vignette)

        p     = self._draw_progress
        pulse = math.sin(self._pulse_tick * 0.15) * 0.5 + 0.5

        for ann in self._annotations:
            t     = ann.get("type")
            color = ANNOTATION_COLORS.get(t, WHITE)
            if t == "arrow":
                self._draw_arrow(painter, ann, color, p)
            elif t == "circle":
                self._draw_circle(painter, ann, color, p, pulse)
            elif t == "box":
                self._draw_box(painter, ann, color, p)
            elif t == "text":
                self._draw_text_ann(painter, ann, p)

        painter.end()

    # ── Painters ──────────────────────────────────────────────────────────

    def _draw_arrow(
        self, painter: QPainter, ann: dict, color: QColor, p: float
    ) -> None:
        x1, y1 = ann.get("x1", 0), ann.get("y1", 0)
        x2, y2 = ann.get("x2", 100), ann.get("y2", 100)
        ex = x1 + (x2 - x1) * p
        ey = y1 + (y2 - y1) * p

        # Glow
        glow = QPen(QColor(color.red(), color.green(), color.blue(), 40), 10)
        glow.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(glow)
        painter.drawLine(int(x1), int(y1), int(ex), int(ey))

        # Line
        pen = QPen(color, 2.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawLine(int(x1), int(y1), int(ex), int(ey))

        if p >= 1.0:
            self._draw_arrowhead(painter, x1, y1, x2, y2, color)
            label = ann.get("label", "")
            if label:
                self._draw_label(
                    painter, (x1 + x2) / 2, (y1 + y2) / 2 - 16, label, color
                )

    def _draw_arrowhead(
        self,
        painter: QPainter,
        x1: float, y1: float,
        x2: float, y2: float,
        color: QColor,
    ) -> None:
        angle = math.atan2(y2 - y1, x2 - x1)
        size  = 13
        a1    = angle + math.pi * 0.8
        a2    = angle - math.pi * 0.8
        pts   = QPolygonF([
            QPointF(x2, y2),
            QPointF(x2 + size * math.cos(a1), y2 + size * math.sin(a1)),
            QPointF(x2 + size * math.cos(a2), y2 + size * math.sin(a2)),
        ])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(color))
        painter.drawPolygon(pts)

    def _draw_circle(
        self,
        painter: QPainter,
        ann:     dict,
        color:   QColor,
        p:       float,
        pulse:   float = 0.0,
    ) -> None:
        x, y   = ann.get("x", 0), ann.get("y", 0)
        r_base = ann.get("r", 44)
        r_now  = r_base * p

        # Outer pulse ring (Clicky style)
        if p >= 1.0:
            pulse_r     = r_base + 12 + pulse * 10
            pulse_alpha = int(80 * (1.0 - pulse))
            painter.setPen(
                QPen(QColor(color.red(), color.green(), color.blue(), pulse_alpha), 2)
            )
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(
                int(x - pulse_r), int(y - pulse_r),
                int(pulse_r * 2),  int(pulse_r * 2),
            )

        # Glow fill
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(
            QBrush(QColor(color.red(), color.green(), color.blue(), 30))
        )
        painter.drawEllipse(
            int(x - r_now), int(y - r_now), int(r_now * 2), int(r_now * 2)
        )

        # Ring
        painter.setPen(QPen(color, 2.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(
            int(x - r_now), int(y - r_now), int(r_now * 2), int(r_now * 2)
        )

        if p >= 1.0:
            label = ann.get("label", "")
            if label:
                self._draw_label(painter, x, y + r_base + 20, label, color)

    def _draw_box(
        self, painter: QPainter, ann: dict, color: QColor, p: float
    ) -> None:
        x, y = ann.get("x", 0), ann.get("y", 0)
        w    = ann.get("w", 100) * p
        h    = ann.get("h",  60) * p
        pen  = QPen(color, 2)
        pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(
            QBrush(QColor(color.red(), color.green(), color.blue(), 18))
        )
        painter.drawRoundedRect(int(x), int(y), int(w), int(h), 6, 6)
        if p >= 1.0 and ann.get("label"):
            self._draw_label(painter, x + w / 2, y - 14, ann["label"], color)

    def _draw_text_ann(self, painter: QPainter, ann: dict, p: float) -> None:
        if p < 0.5:
            return
        x, y  = ann.get("x", 0), ann.get("y", 0)
        alpha = int(min(255, (p - 0.5) * 2 * 220))
        self._draw_label(
            painter, x, y, ann.get("text", ""),
            QColor(255, 255, 255, alpha), large=True,
        )

    def _draw_label(
        self,
        painter: QPainter,
        x:       float,
        y:       float,
        text:    str,
        color:   QColor,
        large:   bool = False,
    ) -> None:
        if not text:
            return
        font = QFont("Segoe UI", 11 if not large else 14)
        font.setBold(True)
        painter.setFont(font)
        fm = painter.fontMetrics()
        tw = fm.horizontalAdvance(text)
        th = fm.height()
        px = int(x - tw / 2)
        py = int(y - th / 2)
        pad = 8

        pill = QPainterPath()
        pill.addRoundedRect(px - pad, py - 4, tw + pad * 2, th + 8, 8, 8)
        painter.fillPath(pill, QColor(0, 0, 0, 150))
        painter.setPen(QPen(QColor(255, 255, 255, 200)))
        painter.drawText(px, py + fm.ascent(), text)
