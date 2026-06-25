"""
ScreenCapture  ─  grabs the primary monitor using mss, resizes to the
configured resolution, and returns a JPEG base64 string.

NOTE: mss instances are NOT thread-safe. We create a new mss context
on every grab() call so it works safely from any thread (voice or hotkey).
"""

import base64
import io
import logging
from typing import Optional

import mss
import mss.tools
from PIL import Image

log = logging.getLogger(__name__)

try:
    import pygetwindow as gw
    _HAS_GW = True
except ImportError:
    _HAS_GW = False


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

        # [FIXED - LOW] was f-string; replaced with % formatting
        log.debug("Screenshot: %s, JPEG=%dKB (b64)", img.size, len(b64) // 1024)
        return b64

    def get_active_window_title(self) -> Optional[str]:
        if not _HAS_GW:
            return None
        try:
            w = gw.getActiveWindow()
            return w.title if w else None
        except Exception:
            return None
