"""
Launch the Vision Assistant UI.
Run from project root: py -3.11 launch.py

FIXES (Session 5):
  - F8/F9 hotkeys now route Qt UI calls via pyqtSignal instead of calling
    widget methods directly from the keyboard thread (was unsafe, random crashes).
  - Screen size read once at startup from Qt instead of opening mss on every
    voice command.

SAFETY FIXES (Session 33):
  - Removed duplicate `from voice.listener import VoiceListener` import
    (VoiceListener was imported twice in main(); only the second survived in
    the original, but the first shadowed is_shutdown_command — fixed by
    merging both into one import line).
  - `import time` moved to module level; the bare `import time` inside
    _do_shutdown() was a local re-import that worked by accident but could
    mask the module-level name in future.
  - bridge.run_query() is called from daemon threads; added a try/except
    wrapper so an unhandled exception in agent code doesn't silently kill
    the thread with no logging.
  - Added null-guard in _handle_command before emitting on_user_query so
    whitespace-only strings don't appear in the log panel.
"""

import sys
import os
import logging
import threading
import time

sys.path.insert(0, os.path.dirname(__file__))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QObject, pyqtSignal

from ui.eye_widget import EyeWidget
from ui.log_panel import LogPanel
from ui.overlay import DrawingOverlay
from ui.text_input import TextInputBar
from ui.stats_bar import StatsBar

log = logging.getLogger(__name__)


class AgentBridge(QObject):
    on_response        = pyqtSignal(str)
    on_user_query      = pyqtSignal(str)
    on_state           = pyqtSignal(str)
    on_annotate        = pyqtSignal(dict)
    on_stats           = pyqtSignal(bool)
    on_overlay_dismiss = pyqtSignal()
    on_hide_ui         = pyqtSignal()
    on_show_ui         = pyqtSignal()
    toggle_text_bar    = pyqtSignal()
    # Safe signal for F9 screen analyze trigger from keyboard thread
    trigger_screen     = pyqtSignal()
    # Safe signal for PTT state change from keyboard thread
    ptt_state          = pyqtSignal(bool)
    # Shutdown: quit the Qt app cleanly so jarvis_launcher widget reappears
    shutdown           = pyqtSignal()
    # Quit signal — must call app.quit() on the main Qt thread
    do_quit            = pyqtSignal()

    def __init__(self, cfg, agent):
        super().__init__()
        self.cfg = cfg
        self.agent = agent

    def run_query(self, user_query=None):
        try:
            self.on_state.emit("thinking")
            self.on_hide_ui.emit()
            time.sleep(0.15)
            parsed = self.agent.analyze_screen(user_query=user_query)
            desc = parsed.get("description", "")
            if desc:
                self.on_response.emit(desc)
            for action in parsed.get("actions", []):
                if action.get("action") == "annotate":
                    self.on_annotate.emit(action)
                elif action.get("action") == "show_stats":
                    self.on_stats.emit(True)
                elif action.get("action") == "hide_stats":
                    self.on_stats.emit(False)
        except Exception:
            log.exception("run_query raised an unhandled exception")
        finally:
            self.on_show_ui.emit()
            self.on_state.emit("idle")

    def run_chat(self, user_query: str):
        """Pure chat — Qwen only, no screen grab, UI stays visible."""
        try:
            self.on_state.emit("thinking")
            result = self.agent.chat(user_query)
            desc = result.get("description", "")
            if desc:
                self.on_response.emit(desc)
        except Exception:
            log.exception("run_chat raised an unhandled exception")
        finally:
            self.on_state.emit("idle")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    from core.config import Config
    from core.agent import Agent
    # Single clean import — was accidentally duplicated in previous version
    from voice.listener import VoiceListener, is_shutdown_command
    from core.pointer import is_point_command, handle as pointer_handle
    import keyboard

    cfg = Config.load()
    agent = Agent(cfg)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    screen = app.primaryScreen().availableGeometry()
    # Read screen size once here — passed to pointer handler, no mss needed per-command
    sw = app.primaryScreen().size().width()
    sh = app.primaryScreen().size().height()

    eye       = EyeWidget()
    log_panel = LogPanel(screen)
    overlay   = DrawingOverlay(screen)
    stats_bar = StatsBar(screen, model_name=cfg.model)

    overlay.hide()
    stats_bar.hide()

    text_bar = TextInputBar(screen)

    bridge = AgentBridge(cfg, agent)

    bridge.on_state.connect(eye.set_state)
    bridge.on_user_query.connect(log_panel.add_user_query)
    bridge.on_response.connect(log_panel.add_ai_response)
    bridge.on_response.connect(lambda _: eye.set_state("speaking"))
    bridge.on_annotate.connect(lambda d: _activate_overlay(overlay, log_panel, eye, d))
    bridge.on_stats.connect(lambda show: stats_bar.show_bar() if show else stats_bar.hide_bar())
    bridge.on_hide_ui.connect(log_panel.hide)
    bridge.on_hide_ui.connect(eye.hide)
    bridge.on_show_ui.connect(log_panel.show)
    bridge.on_show_ui.connect(eye.show)
    bridge.toggle_text_bar.connect(text_bar.toggle)

    # Listener is created here (before _do_shutdown is defined) so the
    # shutdown handler can call listener.stop() to cleanly tear down
    # RealtimeSTT before the process exits.
    listener = VoiceListener(
        on_command=lambda text: _handle_command(text),
        on_ptt_command=lambda text: _handle_command(text),
    )

    # Shutdown: stop the recorder, speak goodbye, then quit —
    # launcher widget reappears automatically once this process exits.
    def _do_shutdown():
        listener.stop()
        agent.dispatcher.speak("Goodbye.")
        time.sleep(1.2)
        # app.quit() MUST run on the main Qt thread — emit the signal
        # instead of calling it directly from this daemon thread.
        bridge.do_quit.emit()

    def _on_shutdown_signal():
        eye.hide()
        log_panel.hide()
        threading.Thread(target=_do_shutdown, daemon=True).start()

    bridge.shutdown.connect(_on_shutdown_signal)
    bridge.do_quit.connect(app.quit)

    # F9 screen analyze — signal fires on keyboard thread, slot runs on main thread
    bridge.trigger_screen.connect(
        lambda: threading.Thread(target=bridge.run_query, daemon=True).start()
    )

    # ------------------------------------------------------------------
    # F9 — manual screen analyze (keyboard thread → Qt signal → main thread)
    # ------------------------------------------------------------------
    keyboard.add_hotkey(cfg.hotkey_screenshot, lambda: bridge.trigger_screen.emit())

    # F10 — text bar toggle
    keyboard.add_hotkey("f10", lambda: bridge.toggle_text_bar.emit(), suppress=True)

    # ------------------------------------------------------------------
    # Voice command handler (runs on its own daemon thread — safe)
    # ------------------------------------------------------------------
    def _handle_command(text: str):
        text = text.strip() if text else ""
        if not text:
            return

        # Check for shutdown before any other routing
        if is_shutdown_command(text):
            log.info("Shutdown command received: %r", text)
            bridge.shutdown.emit()
            return

        bridge.on_user_query.emit(text)
        bridge.on_state.emit("thinking")

        if is_point_command(text):
            log.info("Pointer command: %r", text)

            def _point():
                try:
                    bridge.on_hide_ui.emit()
                    time.sleep(0.1)
                    handled = pointer_handle(
                        text, bridge,
                        speak_fn=agent.dispatcher.speak,
                        screen_size=(sw, sh),
                    )
                    if not handled:
                        bridge.run_query(text)
                except Exception:
                    log.exception("Pointer handler raised an unhandled exception")
                finally:
                    bridge.on_show_ui.emit()
                    bridge.on_state.emit("idle")

            threading.Thread(target=_point, daemon=True).start()
        elif agent.is_screen_query(text):
            # Screen-related command — hide UI while we grab/analyze
            threading.Thread(
                target=bridge.run_query, args=(text,), daemon=True
            ).start()
        else:
            # Pure chat — UI stays visible, no screen grab
            threading.Thread(
                target=bridge.run_chat, args=(text,), daemon=True
            ).start()

    text_bar.command_submitted.connect(
        lambda t: threading.Thread(target=_handle_command, args=(t,), daemon=True).start()
    )

    # F8 PTT — keyboard thread only flips a flag in listener, no Qt calls
    keyboard.add_hotkey("f8", lambda: listener.set_ptt_active(True),  suppress=True)
    keyboard.on_release_key("f8", lambda _: listener.set_ptt_active(False))

    threading.Thread(target=listener.start, daemon=True).start()

    log.info("Vision Assistant started.")
    log.info("Say 'Jarvis <command>' | F8 push-to-talk | F9 screen analyze | F10 text input")
    sys.exit(app.exec())


def _activate_overlay(overlay, log_panel, eye, data):
    log_panel.hide_for_overlay()
    eye.set_state("thinking")
    overlay.show_annotations(data)


if __name__ == "__main__":
    main()
