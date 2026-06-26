"""
actions/dispatcher.py — Routes action dicts to handler functions.

Supported actions:
  click, type, press_key, scroll, move_mouse
  open_app, open_whatsapp, open_url
  volume_up, volume_down, volume_mute
  brightness_up, brightness_down
  todo_add, todo_read, todo_clear, todo_done
  whatsapp_send  (previews message, waits for voice confirm before sending)
  speak, describe
"""

import logging
import os
import re
import sys
import time
import subprocess
import threading
import webbrowser
from pathlib import Path

import pyautogui

log = logging.getLogger(__name__)

pyautogui.FAILSAFE = True
pyautogui.PAUSE    = 0.05

# Must match agent._get_secure_todo_path()
TODO_FILE = Path.home() / "Desktop" / "To-Do.txt"

# Maximum characters a TTS utterance is allowed to reach the TTS engine with.
_TTS_HARD_CAP = 200

# Maximum time (seconds) to wait for WhatsApp window to appear.
_WA_WINDOW_TIMEOUT = 15

_IS_WIN   = sys.platform == "win32"
_IS_MAC   = sys.platform == "darwin"
_IS_LINUX = sys.platform.startswith("linux")

# Linux binary names rarely match friendly names — use a lookup dict.
_APP_MAP_LINUX = {
    "chrome":       "google-chrome-stable",
    "google chrome":"google-chrome-stable",
    "firefox":      "firefox",
    "vscode":       "code",
    "vs code":      "code",
    "terminal":     "gnome-terminal",
    "files":        "nautilus",
    "calculator":   "gnome-calculator",
}


# ── Helpers ───────────────────────────────────────────────────────────────

def _set_speaking(active: bool) -> None:
    """Mute/unmute the STT mic around TTS playback. Lazy import avoids circularity."""
    try:
        from voice.listener import set_speaking
        set_speaking(active)
    except Exception:
        pass   # listener not running (e.g. text-only mode) — safe to ignore


def _whatsapp_exe_path() -> str | None:
    """Return the WhatsApp classic-install .exe path if it exists (Windows only)."""
    if not _IS_WIN:
        return None
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if not local_app_data:
        return None
    candidate = Path(local_app_data) / "WhatsApp" / "WhatsApp.exe"
    return str(candidate) if candidate.exists() else None


# ── Volume helpers (per-platform) ─────────────────────────────────────────

def _volume_change_windows(action: str, amount: int = 1) -> None:
    """Adjust volume on Windows via PowerShell WScript.Shell."""
    keys = {
        "up":   "[char]175",
        "down": "[char]174",
        "mute": "[char]173",
    }
    k = keys.get(action, "")
    if not k:
        return
    if action == "mute" or amount <= 1:
        script = f"(New-Object -ComObject WScript.Shell).SendKeys({k})"
    else:
        script = (
            f"$sh = New-Object -ComObject WScript.Shell; "
            f"1..{amount} | ForEach-Object {{ $sh.SendKeys({k}) }}"
        )
    subprocess.run(["powershell", "-Command", script],
                   capture_output=True, timeout=5)


def _volume_change_mac(action: str, amount: int = 1) -> None:
    """Adjust volume on macOS via osascript."""
    if action == "mute":
        subprocess.run(["osascript", "-e", "set volume output muted true"],
                       capture_output=True, timeout=5)
    elif action == "up":
        subprocess.run(
            ["osascript", "-e",
             f"set volume output volume (output volume of (get volume settings) + {amount * 10})"],
            capture_output=True, timeout=5,
        )
    elif action == "down":
        subprocess.run(
            ["osascript", "-e",
             f"set volume output volume (output volume of (get volume settings) - {amount * 10})"],
            capture_output=True, timeout=5,
        )


def _volume_change_linux(action: str, amount: int = 1) -> None:
    """Adjust volume on Linux via pactl (PulseAudio/PipeWire)."""
    if action == "mute":
        subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"],
                       capture_output=True, timeout=5)
    elif action == "up":
        subprocess.run(
            ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"+{amount * 10}%"],
            capture_output=True, timeout=5,
        )
    elif action == "down":
        subprocess.run(
            ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"-{amount * 10}%"],
            capture_output=True, timeout=5,
        )


def _volume_change(action: str, amount: int = 1) -> None:
    """Dispatch volume change to the correct platform handler."""
    if _IS_WIN:
        _volume_change_windows(action, amount)
    elif _IS_MAC:
        _volume_change_mac(action, amount)
    else:
        _volume_change_linux(action, amount)


# ── Dispatcher ────────────────────────────────────────────────────────────

class ActionDispatcher:
    def __init__(self, cfg) -> None:
        self.cfg  = cfg
        self._tts = _TTSEngine(cfg)
        self._pending_whatsapp: dict | None = None  # queued msg awaiting confirm

    # ── Core dispatch ─────────────────────────────────────────────────────

    def dispatch(self, action: dict) -> dict:
        name = action.get("action", "")
        try:
            handler = getattr(self, f"_do_{name}", None)
            if handler is None:
                log.warning("Unknown action: %r", name)
                return {"ok": False, "action": name, "error": "unknown action"}
            result = handler(action)
            # Handlers that don't return a dict yet are treated as success.
            return result if isinstance(result, dict) else {"ok": True, "action": name}
        except Exception as exc:
            log.exception("Action %r raised an unhandled exception", name)
            return {"ok": False, "action": name, "error": str(exc)}

    def speak(self, text: str) -> None:
        self._tts.say(text)

    # ── Input / automation handlers ───────────────────────────────────────

    def _do_click(self, a: dict) -> dict | None:
        x, y = a.get("x"), a.get("y")
        if x is None or y is None:
            log.warning("click action missing x or y — skipping")
            return {"ok": False, "action": "click", "error": "missing x or y"}
        button = a.get("button", "left")
        if button == "double":
            pyautogui.doubleClick(x, y)
        else:
            pyautogui.click(x, y, button=button)
        log.info("click(%s, %s, %s)", x, y, button)

    def _do_type(self, a: dict) -> None:
        text = a.get("text", "")
        if not text:
            log.warning("type action called with empty 'text'")
            return
        pyautogui.write(text, interval=0.03)
        log.info("type: %s", text[:40])

    def _do_press_key(self, a: dict) -> None:
        key = a.get("key", "")
        if not key:
            log.warning("press_key action called with empty 'key'")
            return
        pyautogui.hotkey(*key.split("+"))
        log.info("press_key: %s", key)

    def _do_scroll(self, a: dict) -> None:
        pyautogui.scroll(a.get("amount", 3), x=a.get("x", 0), y=a.get("y", 0))

    def _do_move_mouse(self, a: dict) -> dict | None:
        x, y = a.get("x"), a.get("y")
        if x is None or y is None:
            log.warning("move_mouse action missing x or y — skipping")
            return {"ok": False, "action": "move_mouse", "error": "missing x or y"}
        pyautogui.moveTo(x, y, duration=0.2)

    def _do_open_app(self, a: dict) -> dict:
        name = str(a.get("name") or "").strip()
        if not name:
            log.warning("open_app called with no 'name' field — skipping")
            return {"ok": False, "action": "open_app", "error": "missing 'name' field"}
        if "whatsapp" in name.lower():
            return self._do_open_whatsapp(a)

        if _IS_WIN:
            # Windows Search
            pyautogui.hotkey("win", "s")
            time.sleep(0.4)
            pyautogui.write(name, interval=0.05)
            time.sleep(0.6)
            pyautogui.press("enter")
        elif _IS_MAC:
            # Spotlight
            pyautogui.hotkey("command", "space")
            time.sleep(0.4)
            pyautogui.write(name, interval=0.05)
            time.sleep(0.6)
            pyautogui.press("enter")
        else:
            # Linux: try binary lookup, then xdg-open, then desktop search (Super key)
            binary = _APP_MAP_LINUX.get(name.lower(), name.lower().replace(" ", "-"))
            try:
                subprocess.Popen([binary])
            except FileNotFoundError:
                log.warning("open_app: binary %r not found — trying Super key search", binary)
                pyautogui.press("super")
                time.sleep(0.5)
                pyautogui.write(name, interval=0.05)
                time.sleep(0.6)
                pyautogui.press("enter")

        log.info("open_app: %s", name)
        return {"ok": True, "action": "open_app", "name": name}

    def _do_open_whatsapp(self, a: dict | None = None) -> dict:
        """Launch WhatsApp desktop app (cross-platform)."""
        if _IS_WIN:
            path = _whatsapp_exe_path()
            if path:
                subprocess.Popen([path])
                log.info("open_whatsapp: launched via %s", path)
            else:
                pyautogui.hotkey("win", "s")
                time.sleep(0.4)
                pyautogui.write("WhatsApp", interval=0.05)
                time.sleep(0.8)
                pyautogui.press("enter")
                log.info("open_whatsapp: launched via Windows Search fallback")
        elif _IS_MAC:
            subprocess.Popen(["open", "-a", "WhatsApp"])
            log.info("open_whatsapp: launched via open -a WhatsApp")
        else:
            # Linux: try native binary, then flatpak, then snap
            for cmd in (
                ["whatsapp-desktop"],
                ["flatpak", "run", "io.github.mimbrero.WhatsAppDesktop"],
                ["snap", "run", "whatsapp-linux-wrapper"],
            ):
                try:
                    subprocess.Popen(cmd)
                    log.info("open_whatsapp: launched via %s", cmd[0])
                    break
                except FileNotFoundError:
                    continue
            else:
                log.warning("open_whatsapp: no WhatsApp binary found on Linux")

        self.speak("Opening WhatsApp.")
        return {"ok": True, "action": "open_app", "name": "WhatsApp"}

    def _do_search_file(self, a: dict) -> None:
        query = a.get("query", "")
        if not query:
            log.warning("search_file called with no 'query' field")
            return

        if _IS_WIN:
            pyautogui.hotkey("win", "e")
            time.sleep(0.5)
            pyautogui.hotkey("ctrl", "f")
            time.sleep(0.2)
            pyautogui.write(query, interval=0.05)
        elif _IS_MAC:
            # Open Finder search via Spotlight
            pyautogui.hotkey("command", "space")
            time.sleep(0.4)
            pyautogui.write(query, interval=0.05)
            time.sleep(0.4)
            pyautogui.press("enter")
        else:
            # Linux: use xdg-open to open home folder, then fall back to Super search
            try:
                subprocess.Popen(["nautilus", "--select", str(Path.home())])
            except FileNotFoundError:
                pass
            pyautogui.press("super")
            time.sleep(0.5)
            pyautogui.write(query, interval=0.05)

    def _do_speak(self, a: dict) -> None:
        self._tts.say(a.get("text", ""))

    def _do_describe(self, a: dict) -> None:
        pass   # handled upstream in agent.py

    # ── Volume ────────────────────────────────────────────────────────────

    def _do_volume_up(self, a: dict) -> None:
        amount = max(1, int(a.get("amount", 5)))
        _volume_change("up", amount)
        log.info("volume up x%d", amount)
        self.speak("Volume increased.")

    def _do_volume_down(self, a: dict) -> None:
        amount = max(1, int(a.get("amount", 5)))
        _volume_change("down", amount)
        log.info("volume down x%d", amount)
        self.speak("Volume decreased.")

    def _do_volume_mute(self, a: dict) -> None:
        _volume_change("mute")
        log.info("volume muted/unmuted")
        self.speak("Volume toggled.")

    # ── Brightness ────────────────────────────────────────────────────────

    def _do_brightness_up(self, a: dict) -> None:
        amount = max(1, int(a.get("amount", 10)))
        try:
            import screen_brightness_control as sbc
            cur = sbc.get_brightness(display=0)[0]
            sbc.set_brightness(min(100, cur + amount), display=0)
            log.info("brightness up: %d → %d", cur, min(100, cur + amount))
            self.speak("Brightness increased.")
        except Exception as exc:
            log.warning("Brightness control error: %s — falling back to keys", exc)
            for _ in range(max(1, amount // 10)):
                pyautogui.press("brightnessup")

    def _do_brightness_down(self, a: dict) -> None:
        amount = max(1, int(a.get("amount", 10)))
        try:
            import screen_brightness_control as sbc
            cur = sbc.get_brightness(display=0)[0]
            sbc.set_brightness(max(10, cur - amount), display=0)
            log.info("brightness down: %d → %d", cur, max(10, cur - amount))
            self.speak("Brightness decreased.")
        except Exception as exc:
            log.warning("Brightness control error: %s — falling back to keys", exc)
            for _ in range(max(1, amount // 10)):
                pyautogui.press("brightnessdown")

    # ── To-do list ────────────────────────────────────────────────────────

    def _do_todo_add(self, a: dict) -> None:
        item = a.get("item", "").strip()
        if not item:
            self.speak("What should I add to your to-do list?")
            return
        try:
            TODO_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(TODO_FILE, "a", encoding="utf-8") as f:
                ts = time.strftime("%Y-%m-%d %H:%M")
                f.write(f"[ ] {item}  ({ts})\n")
            log.info("todo_add: %s", item)
            self.speak(f"Added to your to-do list: {item}")
        except OSError as exc:
            log.error("todo_add failed to write file: %s", exc)
            self.speak("Sorry, I couldn't write to your to-do list.")

    def _do_todo_read(self, a: dict) -> None:
        if not TODO_FILE.exists():
            self.speak("Your to-do list is empty.")
            return
        try:
            lines = TODO_FILE.read_text(encoding="utf-8").strip().splitlines()
        except OSError as exc:
            log.error("todo_read failed to read file: %s", exc)
            self.speak("Sorry, I couldn't read your to-do list.")
            return
        if not lines:
            self.speak("Your to-do list is empty.")
            return
        pending = [l for l in lines if l.startswith("[ ]")]
        done    = [l for l in lines if l.startswith("[x]")]
        summary = f"You have {len(pending)} pending and {len(done)} done tasks."
        if pending:
            items = ". ".join(l[4:].split("(")[0].strip() for l in pending[:3])
            summary += f" Next up: {items}"
            if len(pending) > 3:
                summary += f" and {len(pending) - 3} more."
        self.speak(summary)
        log.info("todo_read: %d pending", len(pending))

    def _do_todo_clear(self, a: dict) -> None:
        try:
            if TODO_FILE.exists():
                TODO_FILE.write_text("", encoding="utf-8")
            self.speak("To-do list cleared.")
            log.info("todo_clear")
        except OSError as exc:
            log.error("todo_clear failed: %s", exc)
            self.speak("Sorry, I couldn't clear your to-do list.")

    def _do_todo_done(self, a: dict) -> None:
        """Mark the first pending item matching a keyword as done."""
        if not TODO_FILE.exists():
            self.speak("No to-do list found.")
            return
        keyword = str(a.get("item", "")).lower()
        if not keyword:
            self.speak("Which item should I mark as done?")
            return
        try:
            lines   = TODO_FILE.read_text(encoding="utf-8").splitlines()
            changed = False
            for i, line in enumerate(lines):
                if line.startswith("[ ]") and keyword in line.lower():
                    lines[i] = "[x]" + line[3:]
                    changed  = True
                    break
            if changed:
                TODO_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
                self.speak("Marked as done.")
            else:
                self.speak(f"Couldn't find '{keyword}' in your list.")
        except OSError as exc:
            log.error("todo_done failed: %s", exc)
            self.speak("Sorry, I couldn't update your to-do list.")

    # ── Open URL ──────────────────────────────────────────────────────────

    def _do_open_url(self, a: dict) -> dict | None:
        url = a.get("url", "").strip()
        if not url:
            log.warning("open_url called with no 'url' field")
            return {"ok": False, "action": "open_url", "error": "missing 'url' field"}
        # Enforce a safe scheme — never allow file://, data://, javascript:, etc.
        if not re.match(r"^https?://", url, re.IGNORECASE):
            url = "https://" + url
        # Validate the result still looks like a URL before handing to the shell.
        if not re.match(r"^https?://[^\s]+$", url, re.IGNORECASE):
            log.warning("open_url: rejected malformed URL %r", url)
            return {"ok": False, "action": "open_url", "error": "malformed URL"}
        # webbrowser.open respects the user's default browser on all platforms.
        # No shell=True, no hard-coded browser binary.
        webbrowser.open(url)
        log.info("open_url: %s", url)
        self.speak(f"Opening {url}.")
        return {"ok": True, "action": "open_url", "url": url}

    # ── WhatsApp send (desktop app, with confirmation) ────────────────────

    def _do_whatsapp_send(self, a: dict) -> None:
        contact = a.get("contact", "").strip()
        message = a.get("message", "").strip()

        # Reject mis-routed commands (e.g. wake-word leaked into contact field).
        if not contact or re.match(r"^jarv", contact, re.IGNORECASE):
            log.warning(
                "whatsapp_send blocked — 'contact' is empty or looks like a "
                "mis-routed command (%r). Check WHATSAPP_RE in agent.py.", contact,
            )
            return

        if not message:
            # No message body — just open the app.
            self._do_open_whatsapp()
            return

        # Store pending BEFORE speaking to prevent a confirm race.
        self._pending_whatsapp = {"contact": contact, "message": message}
        self.speak(
            f"Ready to send a message to {contact}. "
            f"The message is: {message}. "
            "Say confirm to send, or cancel to cancel."
        )
        log.info("whatsapp_send queued: to=%r, msg=%r", contact, message[:40])

    def _launch_whatsapp_desktop(self) -> bool:
        """
        Launch the WhatsApp desktop app and wait until its window is visible.
        Returns True if the window appeared within the timeout, False otherwise.
        """
        if _IS_WIN:
            path = _whatsapp_exe_path()
            if path:
                subprocess.Popen([path])
                log.info("whatsapp: launched via %s", path)
            else:
                pyautogui.hotkey("win", "s")
                time.sleep(0.5)
                pyautogui.write("WhatsApp", interval=0.05)
                time.sleep(0.8)
                pyautogui.press("enter")
                log.info("whatsapp: launched via Windows Search fallback")
        elif _IS_MAC:
            subprocess.Popen(["open", "-a", "WhatsApp"])
            log.info("whatsapp: launched via open -a WhatsApp")
        else:
            for cmd in (
                ["whatsapp-desktop"],
                ["flatpak", "run", "io.github.mimbrero.WhatsAppDesktop"],
                ["snap", "run", "whatsapp-linux-wrapper"],
            ):
                try:
                    subprocess.Popen(cmd)
                    log.info("whatsapp: launched via %s", cmd[0])
                    break
                except FileNotFoundError:
                    continue
            else:
                log.warning("whatsapp: no WhatsApp binary found on Linux")

        # Wait for the WhatsApp window to appear.
        try:
            import pygetwindow as gw
            deadline = time.monotonic() + _WA_WINDOW_TIMEOUT
            while time.monotonic() < deadline:
                wins = gw.getWindowsWithTitle("WhatsApp")
                if wins:
                    win = wins[0]
                    try:
                        win.activate()
                    except Exception:
                        if _IS_WIN:
                            try:
                                import win32gui
                                win32gui.SetForegroundWindow(win._hWnd)
                            except Exception:
                                pass
                        try:
                            win.minimize()
                            time.sleep(0.15)
                            win.restore()
                        except Exception:
                            pass
                    log.info("whatsapp: window found and focused")
                    time.sleep(0.8)
                    return True
                time.sleep(0.5)
            log.warning("whatsapp: window did not appear within %ds", _WA_WINDOW_TIMEOUT)
            return False
        except ImportError:
            log.warning("pygetwindow not available — using fixed 6 s wait")
            time.sleep(6)
            return True

    def has_pending_whatsapp(self) -> bool:
        """True if a WhatsApp message is queued and awaiting confirmation."""
        return self._pending_whatsapp is not None

    def confirm_whatsapp(self) -> None:
        """Called when the user says 'confirm' / 'yes send' / 'go ahead'."""
        if not self._pending_whatsapp:
            self.speak("No pending message to send.")
            return

        contact = self._pending_whatsapp.get("contact", "")
        msg     = self._pending_whatsapp["message"]
        self._pending_whatsapp = None   # clear BEFORE automation — no double-send

        self.speak("Opening WhatsApp, one moment.")

        ready = self._launch_whatsapp_desktop()
        if not ready:
            self.speak("WhatsApp took too long to open. Please try again.")
            return

        if not contact:
            self.speak("No contact name given — please say the name next time.")
            return

        # Search for the contact via the desktop app's search bar.
        pyautogui.hotkey("ctrl", "f")
        time.sleep(0.6)
        pyautogui.write(contact, interval=0.06)
        time.sleep(1.2)
        pyautogui.press("enter")
        time.sleep(0.8)
        log.info("whatsapp: opened chat with %r", contact)

        pyautogui.write(msg, interval=0.04)
        time.sleep(0.3)
        pyautogui.press("enter")

        self.speak(f"Message sent to {contact}.")
        log.info("whatsapp: message sent to %r: %r", contact, msg[:40])

    def cancel_whatsapp(self) -> None:
        """Called when the user says 'cancel'."""
        self._pending_whatsapp = None
        self.speak("Message cancelled.")


# ── TTS engine ────────────────────────────────────────────────────────────

class _TTSEngine:
    def __init__(self, cfg) -> None:
        self.volume = int(cfg.tts_volume * 100)
        self._lock  = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._pyttsx3_engine = None   # lazy-init for macOS/Linux

    def _say_windows(self, safe: str) -> subprocess.Popen:
        script = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"$s.Rate = 2; $s.Volume = {self.volume}; "
            f"$s.Speak('{safe}')"
        )
        return subprocess.Popen(
            ["powershell", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _say_pyttsx3(self, text: str) -> None:
        """Speak via pyttsx3 (macOS / Linux). Runs synchronously inside the thread."""
        try:
            import pyttsx3
            # Re-use engine across calls (init is expensive), but pyttsx3
            # is not thread-safe so we create it inside the lock-protected thread.
            engine = pyttsx3.init()
            engine.setProperty("rate", 175)
            engine.setProperty("volume", self.volume / 100.0)
            engine.say(text)
            engine.runAndWait()
        except Exception:
            log.exception("pyttsx3 TTS error")

    def say(self, text: str) -> None:
        if not text:
            return

        # Hard cap: first sentence only, max 200 chars.
        sentences = re.split(r"(?<=[.!?])\s+", text.replace("\n", " "))
        short = sentences[0].strip()
        if len(short) > _TTS_HARD_CAP:
            short = short[:_TTS_HARD_CAP - 3] + "..."
        if not short:
            return

        # Sanitise — strip characters that would break shell/TTS literals.
        safe = short.replace("'", " ").replace('"', " ").replace("`", " ")

        _set_speaking(True)   # mute mic BEFORE thread starts — no race window

        def _run() -> None:
            proc = None
            try:
                with self._lock:
                    # Kill any still-running previous utterance (Windows only).
                    if self._proc and self._proc.poll() is None:
                        self._proc.kill()
                        self._proc.wait()

                if _IS_WIN:
                    proc = self._say_windows(safe)
                    with self._lock:
                        self._proc = proc
                    proc.wait(timeout=30)
                else:
                    self._say_pyttsx3(safe)
            except subprocess.TimeoutExpired:
                if proc:
                    proc.kill()
                log.warning("TTS killed after 30 s timeout")
            except Exception:
                log.exception("TTS error")
            finally:
                _set_speaking(False)   # always release, even on crash

        threading.Thread(target=_run, daemon=True, name="tts").start()
