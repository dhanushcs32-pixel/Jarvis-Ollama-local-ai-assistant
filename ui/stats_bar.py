"""
ui/stats_bar.py — Glass pill bar showing CPU%, RAM%, GPU%, model name.

Hidden by default. Toggle via agent command or voice "show stats" / "hide stats".
"""

import logging

import psutil
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel
from PyQt6.QtCore    import Qt, QTimer, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui     import QPainter, QColor, QPainterPath, QLinearGradient, QPen

log = logging.getLogger(__name__)

try:
    import GPUtil
    _HAS_GPU = True
except ImportError:
    _HAS_GPU = False


class StatsBar(QWidget):

    def __init__(self, screen_rect, model_name: str = "moondream:1.8b") -> None:
        super().__init__()
        self._screen  = screen_rect
        self._model   = model_name
        self._visible = False

        # Strong references to in-flight animations (prevent GC mid-run).
        self._show_anim: QPropertyAnimation | None = None
        self._hide_anim: QPropertyAnimation | None = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(500, 44)
        self._position()
        self._build_ui()

        # Poll metrics every 2 s — only while the bar is visible.
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._update_metrics)

    # ── Layout ────────────────────────────────────────────────────────────

    def _position(self) -> None:
        x = self._screen.left() + (self._screen.width() - self.width()) // 2
        y = self._screen.bottom() - 60
        self.move(x, y)

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(0)

        style = (
            "color: rgba(255,255,255,200);"
            "font-size: 12px;"
            "font-family: 'SF Mono', 'Consolas', monospace;"
            "background: transparent;"
            "padding: 0 10px;"
        )

        self._model_lbl = self._make_label(f"⬡ {self._model}", style)
        self._sep1      = self._make_label("·", style)
        self._cpu_lbl   = self._make_label("CPU –%", style)
        self._sep2      = self._make_label("·", style)
        self._ram_lbl   = self._make_label("RAM –%", style)
        self._sep3      = self._make_label("·", style)
        self._gpu_lbl   = self._make_label("iGPU –%", style)

        for w in (
            self._model_lbl, self._sep1, self._cpu_lbl,
            self._sep2, self._ram_lbl, self._sep3, self._gpu_lbl,
        ):
            layout.addWidget(w)

        layout.addStretch()

    @staticmethod
    def _make_label(text: str, style: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(style)
        return lbl

    # ── Metrics ───────────────────────────────────────────────────────────

    def _update_metrics(self) -> None:
        try:
            cpu = psutil.cpu_percent(interval=None)
            ram = psutil.virtual_memory().percent
            self._cpu_lbl.setText(f"CPU {cpu:.0f}%")
            self._ram_lbl.setText(f"RAM {ram:.0f}%")
        except Exception:
            log.exception("Failed to read CPU/RAM metrics")

        if _HAS_GPU:
            try:
                gpus = GPUtil.getGPUs()
                if gpus:
                    self._gpu_lbl.setText(f"GPU {gpus[0].load * 100:.0f}%")
            except Exception:
                log.debug("GPU metric unavailable", exc_info=True)

    # ── Paint ─────────────────────────────────────────────────────────────

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), 22, 22)

        painter.fillPath(path, QColor(20, 20, 30, 160))

        grad = QLinearGradient(0, 0, self.width(), 0)
        grad.setColorAt(0,   QColor(60,  160, 255, 30))
        grad.setColorAt(0.5, QColor(255, 255, 255,  8))
        grad.setColorAt(1,   QColor(60,  160, 255, 30))
        painter.setBrush(grad)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(path)

        painter.setPen(QPen(QColor(255, 255, 255, 35), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)
        painter.end()

    # ── Public API ────────────────────────────────────────────────────────

    def toggle(self) -> None:
        if self._visible:
            self.hide_bar()
        else:
            self.show_bar()

    def show_bar(self) -> None:
        if self._visible:
            return
        self._visible = True

        # Cancel any in-progress hide animation.
        if self._hide_anim is not None:
            self._hide_anim.stop()
            self._hide_anim = None

        self._update_metrics()
        self._poll_timer.start(2000)
        self.show()

        anim = QPropertyAnimation(self, b"windowOpacity")
        anim.setDuration(300)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(lambda: setattr(self, "_show_anim", None))
        self._show_anim = anim
        anim.start()

    def hide_bar(self) -> None:
        if not self._visible:
            return
        self._visible = False
        self._poll_timer.stop()   # no point polling while hidden

        # Cancel any in-progress show animation.
        if self._show_anim is not None:
            self._show_anim.stop()
            self._show_anim = None

        anim = QPropertyAnimation(self, b"windowOpacity")
        anim.setDuration(200)
        anim.setStartValue(self.windowOpacity())
        anim.setEndValue(0.0)
        anim.finished.connect(self.hide)
        anim.finished.connect(lambda: setattr(self, "_hide_anim", None))
        self._hide_anim = anim
        anim.start()
