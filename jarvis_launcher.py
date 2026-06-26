"""
jarvis_launcher.py — Jarvis Arc Reactor HUD Launcher Widget
============================================================
A frameless, always-on-top floating desktop widget styled after the
Iron Man arc reactor.  Sits bottom-right by default (draggable).

Behaviour:
  - VISIBLE   → Jarvis is OFF.  Click centre triangle to launch Jarvis.
  - MORPHING  → Shrinks and flies to cursor, then spawns Jarvis.
  - HIDDEN    → Jarvis is ON.  Widget hides; cursor overlay takes over.
  - ON STOP   → Widget reappears at cursor size, grows and flies back to corner.
  - Right-click → context menu: Launch / Quit

Launch:
  py -3.11 jarvis_launcher.py

It spawns launch.py in a subprocess.  When that process ends, the
widget reappears automatically.

Dependencies (already in your env):
  PyQt6
"""

import sys
import os
import subprocess
import threading
import math
import traceback

from PyQt6.QtWidgets import QApplication, QWidget, QMenu
from PyQt6.QtCore    import Qt, QTimer, QPoint, QRectF, QPointF, pyqtSignal, QObject
from PyQt6.QtGui     import (
    QPainter, QColor, QPen, QBrush, QFont, QRadialGradient,
    QConicalGradient, QLinearGradient, QPolygonF, QAction, QCursor,
)

# ── Config ────────────────────────────────────────────────────────────────
WIDGET_SIZE   = 180          # px — full diameter of the widget window
EYE_SIZE      = 62           # px — matches EyeWidget fixed size (SIZE=42 + 20 padding)
DURATION_FRAMES = 48         # 800 ms at ~60 fps
LAUNCH_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "launch.py")

# Colour palette (arc reactor cyan/gold/dark)
C_BG          = QColor(  8,  18,  28, 220)
C_CYAN        = QColor(  0, 210, 255)
C_CYAN_DIM    = QColor(  0, 130, 180, 160)
C_GOLD        = QColor(255, 185,   0)
C_GOLD_DIM    = QColor(180, 120,   0, 160)
C_WHITE       = QColor(230, 245, 255)
C_GLOW        = QColor(  0, 210, 255,  60)
C_INACTIVE    = QColor( 80, 100, 110, 180)


# ── Signals bridge (thread → main thread) ────────────────────────────────
class _Bridge(QObject):
    jarvis_stopped = pyqtSignal()


# ── Main widget ───────────────────────────────────────────────────────────
class ArcReactorWidget(QWidget):
    def __init__(self):
        super().__init__()
        self._bridge = _Bridge()
        self._bridge.jarvis_stopped.connect(self._on_jarvis_stopped)

        self._jarvis_running = False
        self._proc: subprocess.Popen | None = None

        # Animation state
        self._outer_angle  = 0
        self._middle_angle = 0
        self._pulse        = 0.0
        self._pulse_dir    = 1
        self._hover        = False

        # Drag state
        self._drag_pos: QPoint | None = None

        # Morph state (forward = shrink to cursor; reverse = grow back to corner)
        self._morphing:      bool         = False
        self._morph_reverse: bool         = False
        self._morph_t:       float        = 0.0
        self._morph_start:   QPoint | None = None   # widget top-left when morph began
        self._corner_pos:    QPoint | None = None   # saved corner position for return

        self._setup_window()
        self._setup_timer()

    # ── Window setup ──────────────────────────────────────────────────────

    def _setup_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool               |
            Qt.WindowType.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setFixedSize(WIDGET_SIZE, WIDGET_SIZE)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.right()  - WIDGET_SIZE - 24,
                  screen.bottom() - WIDGET_SIZE - 24)

    # ── Animation timer ───────────────────────────────────────────────────

    def _setup_timer(self):
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)   # ~60 fps

    def _tick(self):
        self._outer_angle  = (self._outer_angle  + 0.4) % 360
        self._middle_angle = (self._middle_angle - 0.8) % 360
        self._pulse        += 0.018 * self._pulse_dir
        if self._pulse >= 1.0:
            self._pulse = 1.0; self._pulse_dir = -1
        elif self._pulse <= 0.0:
            self._pulse = 0.0; self._pulse_dir =  1

        if self._morphing:
            self._morph_step()

        self.update()

    # ── Morph: fly to cursor then launch ─────────────────────────────────

    def _begin_morph_forward(self):
        """Called on click. Shrink + fly to cursor, then spawn Jarvis."""
        self._corner_pos   = self.pos()          # remember where to return to
        self._morph_start  = self.pos()
        self._morph_t      = 0.0
        self._morph_reverse = False
        self._morphing     = True

    def _begin_morph_reverse(self):
        """Called when Jarvis stops. Appear at cursor, grow + fly back to corner."""
        cursor = QCursor.pos()
        # Place widget centred on cursor at eye size
        self.setFixedSize(EYE_SIZE, EYE_SIZE)
        self.move(cursor.x() - EYE_SIZE // 2, cursor.y() - EYE_SIZE // 2)
        self.show()

        self._morph_start   = self.pos()
        self._morph_t       = 0.0
        self._morph_reverse = True
        self._morphing      = True

    def _morph_step(self):
        self._morph_t = min(1.0, self._morph_t + 1.0 / DURATION_FRAMES)

        # Smoothstep easing
        t    = self._morph_t
        ease = t * t * (3 - 2 * t)

        if not self._morph_reverse:
            # FORWARD: WIDGET_SIZE → EYE_SIZE, corner → cursor
            cursor   = QCursor.pos()
            cur_size = int(WIDGET_SIZE + (EYE_SIZE - WIDGET_SIZE) * ease)
            tx = cursor.x() - cur_size // 2
            ty = cursor.y() - cur_size // 2
            sx = self._morph_start.x()
            sy = self._morph_start.y()
            nx = int(sx + (tx - sx) * ease)
            ny = int(sy + (ty - sy) * ease)

        else:
            # REVERSE: EYE_SIZE → WIDGET_SIZE, cursor → corner
            cur_size = int(EYE_SIZE + (WIDGET_SIZE - EYE_SIZE) * ease)
            corner   = self._corner_pos
            sx       = self._morph_start.x()
            sy       = self._morph_start.y()
            # target top-left so full widget lands at corner
            tx = corner.x()
            ty = corner.y()
            nx = int(sx + (tx - sx) * ease)
            ny = int(sy + (ty - sy) * ease)

        self.setFixedSize(cur_size, cur_size)
        self.move(nx, ny)

        if self._morph_t >= 1.0:
            self._morphing = False
            if not self._morph_reverse:
                # Forward done — hide and spawn Jarvis
                self.hide()
                self._spawn_jarvis()
            else:
                # Reverse done — snap to exact corner size and pos
                self.setFixedSize(WIDGET_SIZE, WIDGET_SIZE)
                self.move(self._corner_pos)

    # ── Paint ─────────────────────────────────────────────────────────────

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx, cy = self.width() / 2, self.height() / 2
        p.translate(cx, cy)

        # Scale artwork to fit current window size during morph
        if self._morphing:
            scale = self.width() / WIDGET_SIZE
            p.scale(scale, scale)

        self._draw_bg(p)
        self._draw_hex_grid(p)
        self._draw_outer_ring(p)
        self._draw_gold_ring(p)
        self._draw_middle_ring(p)
        self._draw_inner_ring(p)
        self._draw_core(p)
        self._draw_triangle(p)
        self._draw_labels(p)

        # Fade artwork out: start fading at 30%, fully black by 60%, stay black to end
        if self._morphing and not self._morph_reverse:
            if self._morph_t > 0.30:
                fade = min(1.0, (self._morph_t - 0.30) / 0.30)   # 0→1 over 30%-60%
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(QColor(0, 0, 0, int(220 * fade))))
                r = WIDGET_SIZE / 2
                p.drawEllipse(QRectF(-r, -r, r * 2, r * 2))

        # Fade artwork in on reverse: stay black until 60%, then reveal by 90%
        if self._morphing and self._morph_reverse:
            if self._morph_t < 0.90:
                fade = 1.0 - max(0.0, (self._morph_t - 0.60) / 0.30)  # 1→0 over 60%-90%
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(QColor(0, 0, 0, int(220 * fade))))
                r = WIDGET_SIZE / 2
                p.drawEllipse(QRectF(-r, -r, r * 2, r * 2))

        p.end()

    def _draw_bg(self, p: QPainter):
        r = WIDGET_SIZE / 2 - 2
        grad = QRadialGradient(QPointF(0, 0), r)
        grad.setColorAt(0.0, QColor(10, 30, 50, 200))
        grad.setColorAt(0.7, QColor( 5, 15, 25, 230))
        grad.setColorAt(1.0, QColor( 2,  8, 15, 240))
        p.setBrush(QBrush(grad))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(-r, -r, r*2, r*2))

    def _draw_hex_grid(self, p: QPainter):
        """Faint hexagonal background texture."""
        pen = QPen(QColor(0, 150, 180, 25))
        pen.setWidthF(0.5)
        p.setPen(pen)
        step = 10
        for row in range(-8, 9):
            for col in range(-8, 9):
                x = col * step + (step/2 if row % 2 else 0)
                y = row * step * 0.866
                if x*x + y*y < (WIDGET_SIZE/2 - 4)**2:
                    self._draw_hex(p, x, y, 4.5)

    def _draw_hex(self, p, cx, cy, r):
        pts = []
        for i in range(6):
            a = math.radians(60 * i - 30)
            pts.append(QPointF(cx + r * math.cos(a), cy + r * math.sin(a)))
        p.drawPolygon(QPolygonF(pts))

    def _draw_outer_ring(self, p: QPainter):
        """Outermost cyan segmented ring — rotates clockwise."""
        r = WIDGET_SIZE / 2 - 4
        p.save()
        p.rotate(self._outer_angle)
        pen = QPen(C_CYAN)
        pen.setWidthF(2.5)
        p.setPen(pen)
        seg, gap = 13, 2
        for i in range(24):
            start = i * 15 + gap
            span  = 15 - gap * 2
            p.drawArc(QRectF(-r, -r, r*2, r*2),
                      int(start * 16), int(span * 16))
        pen2 = QPen(C_CYAN_DIM)
        pen2.setWidthF(1)
        p.setPen(pen2)
        for i in range(48):
            a = math.radians(i * 7.5)
            inner = r - 6
            outer = r + 1
            p.drawLine(
                QPointF(inner * math.cos(a), inner * math.sin(a)),
                QPointF(outer * math.cos(a), outer * math.sin(a)),
            )
        p.restore()

    def _draw_gold_ring(self, p: QPainter):
        """Gold segmented ring — static but pulses."""
        r = WIDGET_SIZE / 2 - 14
        alpha = int(160 + 80 * self._pulse)
        gold = QColor(255, 185, 0, alpha)
        pen = QPen(gold)
        pen.setWidthF(9)
        p.setPen(pen)
        for i in range(16):
            start = i * 22.5 + 2
            span  = 22.5 - 4
            p.drawArc(QRectF(-r, -r, r*2, r*2),
                      int(start * 16), int(span * 16))

    def _draw_middle_ring(self, p: QPainter):
        """Cyan ring — rotates counter-clockwise."""
        r = WIDGET_SIZE / 2 - 30
        p.save()
        p.rotate(self._middle_angle)
        pen = QPen(C_CYAN)
        pen.setWidthF(1.5)
        p.setPen(pen)
        for i in range(12):
            start = i * 30 + 3
            span  = 24
            p.drawArc(QRectF(-r, -r, r*2, r*2),
                      int(start * 16), int(span * 16))
        p.restore()

    def _draw_inner_ring(self, p: QPainter):
        """Inner solid cyan circle border."""
        r = WIDGET_SIZE / 2 - 44
        glow_alpha = int(40 + 40 * self._pulse)
        pen_glow = QPen(QColor(0, 210, 255, glow_alpha))
        pen_glow.setWidthF(8)
        p.setPen(pen_glow)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(-r, -r, r*2, r*2))
        pen = QPen(C_CYAN)
        pen.setWidthF(1.5)
        p.setPen(pen)
        p.drawEllipse(QRectF(-r, -r, r*2, r*2))

    def _draw_core(self, p: QPainter):
        """Radial glow core."""
        r = WIDGET_SIZE / 2 - 50
        grad = QRadialGradient(QPointF(0, 0), r)
        pulse_alpha = int(180 + 60 * self._pulse)
        grad.setColorAt(0.0, QColor(0, 220, 255, pulse_alpha))
        grad.setColorAt(0.4, QColor(0, 160, 200, 120))
        grad.setColorAt(1.0, QColor(0,  60,  90,  20))
        p.setBrush(QBrush(grad))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(-r, -r, r*2, r*2))

    def _draw_triangle(self, p: QPainter):
        """Downward-pointing triangle (arc reactor / Jarvis logo)."""
        size = 18
        glow_alpha = int(200 + 55 * self._pulse)
        pen_glow = QPen(QColor(0, 230, 255, 80))
        pen_glow.setWidthF(6)
        p.setPen(pen_glow)
        p.setBrush(Qt.BrushStyle.NoBrush)
        tri_glow = QPolygonF([
            QPointF(0, size + 2),
            QPointF(-size - 2, -size//2 - 2),
            QPointF( size + 2, -size//2 - 2),
        ])
        p.drawPolygon(tri_glow)
        pen = QPen(QColor(0, 230, 255, glow_alpha))
        pen.setWidthF(2)
        p.setPen(pen)
        tri = QPolygonF([
            QPointF(0,      size),
            QPointF(-size, -size//2),
            QPointF( size, -size//2),
        ])
        if self._hover and not self._jarvis_running:
            p.setBrush(QBrush(QColor(0, 230, 255, 60)))
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPolygon(tri)

    def _draw_labels(self, p: QPainter):
        """SIGNAL top, ENERGY LEVEL bottom, WIFI left — faint gold text."""
        import sys as _sys
        _mono = ("Menlo" if _sys.platform == "darwin"
                 else "Consolas" if _sys.platform == "win32"
                 else "DejaVu Sans Mono")
        font = QFont(_mono, 5, QFont.Weight.Bold)
        p.setFont(font)
        alpha = int(140 + 60 * self._pulse)
        pen = QPen(QColor(255, 185, 0, alpha))
        p.setPen(pen)

        r = WIDGET_SIZE / 2 - 8

        p.save()
        p.rotate(-90)
        p.drawText(QRectF(r - 36, -6, 36, 12),
                   Qt.AlignmentFlag.AlignCenter, "SIGNAL")
        p.restore()

        p.save()
        p.rotate(90)
        p.drawText(QRectF(r - 52, -6, 52, 12),
                   Qt.AlignmentFlag.AlignCenter, "ENERGY LEVEL")
        p.restore()

        p.save()
        p.rotate(180)
        p.drawText(QRectF(r - 26, -6, 26, 12),
                   Qt.AlignmentFlag.AlignCenter, "WIFI")
        p.restore()

        status_col = C_CYAN if not self._jarvis_running else C_GOLD
        p.setBrush(QBrush(status_col))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(-3, 26, 6, 6))

    # ── Mouse events ──────────────────────────────────────────────────────

    def mousePressEvent(self, e):
        if self._morphing:
            return   # ignore clicks during animation
        if e.button() == Qt.MouseButton.LeftButton:
            cx, cy = WIDGET_SIZE / 2, WIDGET_SIZE / 2
            dx = e.position().x() - cx
            dy = e.position().y() - cy
            in_centre = dx*dx + dy*dy < 32*32
            if in_centre:
                if not self._jarvis_running:
                    self._begin_morph_forward()   # fly to cursor, THEN launch
            else:
                self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        elif e.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(e.globalPosition().toPoint())

    def mouseMoveEvent(self, e):
        if self._drag_pos and e.buttons() == Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
            # Keep corner_pos in sync if user dragged before launching
            self._corner_pos = self.pos()

    def mouseReleaseEvent(self, e):
        self._drag_pos = None

    def enterEvent(self, e):
        self._hover = True

    def leaveEvent(self, e):
        self._hover = False

    # ── Context menu ──────────────────────────────────────────────────────

    def _show_context_menu(self, pos: QPoint):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background: #0a121e;
                border: 1px solid #00c8ff;
                color: #00c8ff;
                font-family: Consolas;
                font-size: 11px;
            }
            QMenu::item:selected { background: #00394f; }
        """)
        if not self._jarvis_running:
            act_launch = QAction("⚡  Launch Jarvis", self)
            act_launch.triggered.connect(self._begin_morph_forward)
            menu.addAction(act_launch)
        else:
            act_status = QAction("● Jarvis is running", self)
            act_status.setEnabled(False)
            menu.addAction(act_status)
        menu.addSeparator()
        act_quit = QAction("✕  Quit Launcher", self)
        act_quit.triggered.connect(QApplication.quit)
        menu.addAction(act_quit)
        menu.exec(pos)

    # ── Jarvis process management ─────────────────────────────────────────

    def _spawn_jarvis(self):
        """Actually start the subprocess. Called after forward morph completes."""
        self._jarvis_running = True

        def _run():
            try:
                self._proc = subprocess.Popen(
                    [sys.executable, LAUNCH_SCRIPT],
                    cwd=os.path.dirname(LAUNCH_SCRIPT),
                )
                self._proc.wait()
            except Exception as ex:
                print(f"[launcher] Failed to start Jarvis: {ex}")
                traceback.print_exc()
            finally:
                self._bridge.jarvis_stopped.emit()

        threading.Thread(target=_run, daemon=True).start()

    def _on_jarvis_stopped(self):
        """Called on main thread when Jarvis process exits."""
        self._jarvis_running = False
        self._proc = None
        self._begin_morph_reverse()   # grow back to corner


# ── Entry point ───────────────────────────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    widget = ArcReactorWidget()
    widget.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
