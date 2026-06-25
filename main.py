"""
Vision Assistant - Main Orchestrator
Run this file to start the assistant. Press F9 for screen capture, hold F8 to speak.

FIXES (Session 27):
  - VoiceListener(cfg, agent) -> VoiceListener(on_command, on_ptt_command)
  - Pointer routing added as priority-1 in on_command().
  - F8 PTT wiring uses same on_command routing.
  - screen_size obtained once from mss.

SAFETY FIXES (Session 33):
  - Removed `from voice.listener import set_speaking` — that symbol does not
    exist in listener.py (it was an import leftover from an old API). Would
    raise ImportError on startup.
  - on_command() now strips and null-guards text before any routing.
  - Wrapped on_screenshot() in try/except so an F9 crash doesn't silently
    kill the hotkey.
  - _speak() already had a try/except — kept and tightened.
  - keyboard.wait() is the blocking call; wrapped in KeyboardInterrupt only
    (was already there) — no change needed there.
  - Added type annotation on on_command for clarity.
"""

import threading
import logging
import keyboard

from core.agent import Agent
from core.config import Config
from core import pointer
from voice.listener import VoiceListener   # set_speaking removed — doesn't exist
from utils.logger import setup_logger

log = setup_logger(__name__)


def main():
    cfg = Config.load()
    agent = Agent(cfg)

    log.info("Vision Assistant started.")
    log.info(f"  Model     : {cfg.model}")
    log.info(f"  Hotkey    : {cfg.hotkey_screenshot} = analyze screen")
    log.info(f"  Hold key  : {cfg.hotkey_voice}      = speak command")
    log.info("Press Ctrl+C to quit.\n")

    # ------------------------------------------------------------------
    # Resolve screen size once (used by pointer overlay arrow origin)
    # ------------------------------------------------------------------
    try:
        import mss
        with mss.mss() as sct:
            m = sct.monitors[1]  # primary monitor
            screen_size = (m["width"], m["height"])
    except Exception:
        screen_size = (1920, 1080)  # safe fallback
    log.info(f"Screen size: {screen_size}")

    # ------------------------------------------------------------------
    # bridge — the Qt signal bus that pointer.handle() emits on.
    # ------------------------------------------------------------------
    try:
        bridge = agent.bridge
    except AttributeError:
        class _BridgeStub:
            class _Sig:
                def emit(self, *a, **kw):
                    log.debug("bridge.on_annotate.emit called (stub): %s", a)
            on_annotate = _Sig()
        bridge = _BridgeStub()
        log.warning("agent.bridge not found — using stub bridge. Overlay arrows won't render.")

    # ------------------------------------------------------------------
    # speak helper — used by pointer.handle() for TTS feedback
    # ------------------------------------------------------------------
    def _speak(text: str):
        try:
            agent.dispatcher.speak(text)
        except Exception as e:
            log.warning("speak error: %s", e)

    # ------------------------------------------------------------------
    # on_command — central routing for ALL voice/text commands
    # Priority:  1. pointer
    #            2. agent.analyze_screen()
    #            3. agent.chat()
    # ------------------------------------------------------------------
    def on_command(text: str) -> None:
        text = text.strip() if text else ""
        if not text:
            return

        # 1. Pointer / word-find commands
        if pointer.is_point_command(text):
            log.info("Routed to pointer: %r", text)
            try:
                pointer.handle(text, bridge, _speak, screen_size, cfg)
            except Exception:
                log.exception("pointer.handle raised an unhandled exception")
            return

        # 2. Screen-analysis commands
        try:
            from core.agent import is_screen_query  # avoids circular import at module level
            if is_screen_query(text):
                log.info("Routed to analyze_screen: %r", text)
                agent.analyze_screen(user_query=text)
                return
        except ImportError:
            pass  # is_screen_query not yet extracted — fall through to chat

        # 3. General chat / system commands
        log.info("Routed to chat: %r", text)
        try:
            agent.chat(text)
        except Exception:
            log.exception("agent.chat raised an unhandled exception")

    # ------------------------------------------------------------------
    # Voice listener
    # ------------------------------------------------------------------
    if cfg.voice_enabled:
        voice = VoiceListener(
            on_command=on_command,
            on_ptt_command=on_command,
        )
        t = threading.Thread(target=voice.start, daemon=True)
        t.start()
        log.info("Voice listener active.")

    # ------------------------------------------------------------------
    # F9 hotkey: capture + analyze screen (no spoken query)
    # ------------------------------------------------------------------
    def on_screenshot():
        log.info("[F9] Screen analysis triggered.")
        try:
            agent.analyze_screen(user_query=None)
        except Exception:
            log.exception("analyze_screen raised an unhandled exception")

    keyboard.add_hotkey(cfg.hotkey_screenshot, on_screenshot)

    try:
        keyboard.wait()
    except KeyboardInterrupt:
        log.info("Shutting down.")


if __name__ == "__main__":
    main()
