"""
ScreenCapture  ─  grabs the primary monitor using mss, resizes to the
configured resolution, and returns a JPEG base64 string.

NOTE: mss instances are NOT thread-safe. We create a new mss context
on every grab() call so it works safely from any thread (voice or hotkey).

Platform support for get_active_window_title():
  - Windows : pygetwindow
  - macOS   : AppKit (NSWorkspace)
  - Linux   : xdotool via subprocess (X11/XWayland)
"""

import base64
import io
import logging
import subprocess
import sys
from typing import Optional

import mss
import mss.tools
from PIL import Image

log = logging.getLogger(__name__)

# ── Windows ──────────────────────────────────────────────────────────────────
try:
    import pygetwindow as gw
    _HAS_GW = True
except ImportError:
    _HAS_GW = False

# ── macOS ─────────────────────────────────────────────────────────────────────
try:
    if sys.platform == "darwin":
        from AppKit import NSWorkspace  # type: ignore
        _HAS_APPKIT = True
    else:
        _HAS_APPKIT = False
except ImportError:
    _HAS_APPKIT = False


def _get_title_windows() -> Optional[str]:
    """Active window title on Windows via pygetwindow."""
    if not _HAS_GW:
        return None
    try:
        w = gw.getActiveWindow()
        return w.title if w else None
    except Exception:
        return None


def _get_title_macos() -> Optional[str]:
    """Active window title on macOS via AppKit."""
    if not _HAS_APPKIT:
        return None
    try:
        active_app = NSWorkspace.sharedWorkspace().activeApplication()
        return active_app.get("NSApplicationName")
    except Exception:
        return None


def _get_title_linux() -> Optional[str]:
    """Active window title on Linux via xdotool (X11 / XWayland).

    Requires xdotool to be installed:
        sudo apt install xdotool   # Debian/Ubuntu
        sudo dnf install xdotool   # Fedora
    Returns None if xdotool is missing or the call fails.
    """
    try:
        win_id = subprocess.check_output(
            ["xdotool", "getactivewindow"],
            stderr=subprocess.DEVNULL,
            timeout=1,
        ).strip()
        title = subprocess.check_output(
            ["xdotool", "getwindowname", win_id],
            stderr=subprocess.DEVNULL,
            timeout=1,
        )
        return title.decode().strip() or None
    except (FileNotFoundError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired, ValueError):
        return None


# Pick the right implementation once at import time
if sys.platform == "win32":
    _get_active_title = _get_title_windows
elif sys.platform == "darwin":
    _get_active_title = _get_title_macos
else:
    # Linux and any other POSIX platform
    _get_active_title = _get_title_linux


class ScreenCapture:
    def __init__(self, cfg):
        self.cfg = cfg

    def grab_base64(self, max_width: Optional[int] = None) -> str:
        """Capture primary monitor, resize, encode to JPEG base64.
        Creates a fresh mss instance each call — safe from any thread.

        Args:
            max_width: If set, caps the output width (height scaled proportionally).
                       Overrides cfg.capture_width/height when smaller — useful for
                       fast moondream queries that don't need full resolution.
        """
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            raw = sct.grab(monitor)

        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")

        # Determine target size — max_width overrides config when it's smaller
        cfg_w, cfg_h = self.cfg.capture_width, self.cfg.capture_height
        if max_width and max_width < cfg_w:
            scale = max_width / cfg_w
            target = (max_width, max(1, int(cfg_h * scale)))
        else:
            target = (cfg_w, cfg_h)

        if img.size != target:
            img = img.resize(target, Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=self.cfg.capture_quality, optimize=True)
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

        log.debug("Screenshot: %s, JPEG=%dKB (b64)", img.size, len(b64) // 1024)
        return b64

    def get_active_window_title(self) -> Optional[str]:
        """Return the title of the currently focused window, or None if unavailable."""
        return _get_active_title()
