"""
app.py — PyQt6 UI launcher for the Vision Assistant.

Run this instead of main.py. It starts the QApplication, launches all
UI widgets, and runs the agent + voice listener in background threads.

Usage:
    py -3.11 app.py
"""

import sys
import logging
import threading

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QScreen

log = logging.getLogger(__name__)


# ── Agent bridge (runs in background thread, emits Qt signals) ────────────────

class AgentBridge(QObject):
    """
    Wraps the Agent so it can run in a QThread and emit signals
    that the UI widgets can connect to safely (cross-thread).
    """
    on_response        = pyqtSignal(str)   # AI text response
    on_user_query      = pyqtSignal(str)   # transcribed voice / text command
    on_state           = pyqtSignal(str)   # eye state: idle/listening/thinking/speaking
    on_annotate        = pyqtSignal(dict)  # annotation data for overlay
    on_stats           = pyqtSignal(bool)  # True=show stats, False=hide
    on_overlay_dismiss = pyqtSignal()      # dismiss drawing overlay

    def __init__(self, cfg, agent):
        super().__init__()
        self.cfg   = cfg
        self.agent = agent

    def run_query(self, user_query: str | None = None) -> None:
        """Called from voice listener or hotkey. Runs in a worker thread."""
        try:
            self.on_state.emit("thinking")
            parsed = self.agent.analyze_screen(user_query=user_query)

            desc = parsed.get("description", "")
            if desc:
                self.on_response.emit(desc)

            for action in parsed.get("actions", []):
                act = action.get("action")
                if act == "annotate":
                    self.on_annotate.emit(action)
                elif act == "show_stats":
                    self.on_stats.emit(True)
                elif act == "hide_stats":
                    self.on_stats.emit(False)
        except Exception:
            log.exception("AgentBridge.run_query raised an unhandled exception")
        finally:
            # Always return to idle — even if the agent raised.
            self.on_state.emit("idle")


# ── Main entry point ──────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    from core.config import Config
    from core.agent  import Agent

    cfg   = Config.load()
    agent = Agent(cfg)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # keep alive even if all windows close

    screen = app.primaryScreen().availableGeometry()

    # ── Instantiate UI widgets ────────────────────────────────────────────
    from ui.eye_widget  import EyeWidget
    from ui.log_panel   import LogPanel
    from ui.overlay     import DrawingOverlay
    from ui.stats_bar   import StatsBar
    from ui.text_input  import TextInputBar

    eye       = EyeWidget()
    log_panel = LogPanel(screen)
    overlay   = DrawingOverlay(screen)
    stats_bar = StatsBar(screen, model_name=cfg.model)
    text_bar  = TextInputBar(screen)

    overlay.hide()
    stats_bar.hide()

    # ── Wire agent bridge signals to UI ───────────────────────────────────
    bridge = AgentBridge(cfg, agent)

    bridge.on_state.connect(eye.set_state)

    bridge.on_user_query.connect(log_panel.add_user_query)

    # Response: log it, then transition eye idle→speaking→idle is managed
    # inside run_query via on_state; set speaking here for the duration.
    bridge.on_response.connect(log_panel.add_ai_response)
    bridge.on_response.connect(lambda _: eye.set_state("speaking"))

    bridge.on_annotate.connect(
        lambda data: _activate_overlay(overlay, log_panel, eye, data)
    )
    bridge.on_overlay_dismiss.connect(
        lambda: _dismiss_overlay(overlay, log_panel)
    )

    bridge.on_stats.connect(
        lambda show: stats_bar.show_bar() if show else stats_bar.hide_bar()
    )

    # ── F10 text input bar ────────────────────────────────────────────────
    def _on_text_command(text: str) -> None:
        """Fired from TextInputBar.command_submitted (main thread)."""
        bridge.on_user_query.emit(text)
        t = threading.Thread(
            target=bridge.run_query, args=(text,), daemon=True, name="agent-text"
        )
        t.start()

    text_bar.command_submitted.connect(_on_text_command)

    # ── Hotkeys ───────────────────────────────────────────────────────────
    import keyboard

    def _on_f9() -> None:
        """F9 = screenshot-analyze. keyboard callback runs in a non-Qt thread."""
        t = threading.Thread(
            target=bridge.run_query, daemon=True, name="agent-f9"
        )
        t.start()

    def _on_f10() -> None:
        """F10 = toggle text input bar. Must marshal to Qt main thread."""
        # QTimer.singleShot(0, …) schedules a call on the Qt event loop from
        # any thread — the only safe way to touch widgets outside the main thread.
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(0, text_bar.toggle)

    keyboard.add_hotkey(cfg.hotkey_screenshot, _on_f9)
    keyboard.add_hotkey("f10", _on_f10)

    # ── Voice listener in daemon thread ───────────────────────────────────
    from voice.listener import VoiceListener, is_shutdown_command

    def _on_transcription(text: str) -> None:
        """VoiceListener callback — runs in background STT thread."""
        stripped = text.strip()
        if not stripped:
            return
        if is_shutdown_command(stripped):
            log.info("Shutdown command received via voice.")
            app.quit()
            return
        bridge.on_user_query.emit(stripped)
        t = threading.Thread(
            target=bridge.run_query, args=(stripped,), daemon=True, name="agent-voice"
        )
        t.start()

    voice_listener = VoiceListener(cfg, agent, on_transcription=_on_transcription)
    voice_thread   = threading.Thread(
        target=voice_listener.run, daemon=True, name="voice-listener"
    )
    voice_thread.start()

    # ── Clean shutdown ────────────────────────────────────────────────────
    def _on_quit() -> None:
        log.info("Shutting down voice listener…")
        try:
            voice_listener.stop()
        except Exception:
            log.exception("Error stopping voice listener")

    app.aboutToQuit.connect(_on_quit)

    log.info(
        "UI started. Eye widget active. "
        "Hold F8 to speak, F9 to analyze screen, F10 for text input."
    )
    sys.exit(app.exec())


# ── Overlay helpers ───────────────────────────────────────────────────────────

def _activate_overlay(
    overlay,
    log_panel,
    eye,
    data: dict,
) -> None:
    log_panel.hide_for_overlay()
    eye.set_state("thinking")
    overlay.show_annotations(data)


def _dismiss_overlay(overlay, log_panel) -> None:
    overlay.dismiss()
    log_panel.show_after_overlay()


if __name__ == "__main__":
    main()
