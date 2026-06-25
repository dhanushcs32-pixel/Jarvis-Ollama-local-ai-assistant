"""
IconFinder v5 — splits lookup by query type, vision as last resort.

Strategy order:
  1. Icon mode  — query matches a known app/icon name (APP_ALIASES) → pywinauto
                  Shell API desktop icon lookup. Instant, exact names, no OCR.
  2. Word mode  — anything else (arbitrary word/phrase) → tiled multi-pass OCR
                  + NMS across the full screen. Slower but reads any text.
  3. Vision     — fallback only when both above find nothing (unlabelled icons,
                  appearance-based queries like "the red button").
"""

import logging
import re
import os
import io
import base64
import time
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image

log = logging.getLogger(__name__)

TESSERACT_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
]

APP_ALIASES = {
    "firefox":       ["firefox", "mozilla"],
    "chrome":        ["chrome", "google chrome"],
    "edge":          ["edge", "microsoft edge"],
    "vscode":        ["code", "visual studio code"],
    "visual studio": ["visual studio", "code"],
    "terminal":      ["terminal", "powershell", "cmd"],
    "explorer":      ["explorer", "file explorer", "this pc"],
    "solid edge":    ["solid", "solidedge", "st9", "solid edge"],
    "solidedge":     ["solid", "solidedge", "st9"],
    "keyshot":       ["keyshot"],
    "arduino":       ["arduino", "arduino ide"],
    "mcafee":        ["mcafee"],
    "roamer":        ["roamer"],
    "antigravity":   ["antigravity", "anti-gravity", "anti gravity"],
    "recycle bin":   ["recycle", "bin"],
    "gta":           ["grand theft", "gta", "auto"],
    "notepad":       ["notepad"],
    "discord":       ["discord"],
    "grand theft auto": ["grand theft", "gta"],
}

TERMINAL_NOISE = [
    "info", "warn", "error", "debug", "traceback", "import",
    "def ", "return", "transcrib", "whisper", "ollama", "http",
    "core.", "voice.", "ps c:\\", ">>>", "exception",
]

# Tiled word-mode settings
TILE_W       = 300
TILE_H       = 150
OVERLAP_X    = 100
OVERLAP_Y    = 50
TILE_WORKERS = 4
CONF_THRESHOLD     = 30
NMS_IOU_THRESHOLD  = 0.3
WORD_PASSES = [
    ("a_2x_grey",  2.0),
    ("c_clahe_2x", 2.0),
    ("d_thresh",   2.0),
]


class IconFinder:
    def __init__(self, ollama_host="http://localhost:11434", model="moondream:1.8b", callback=None):
        self.ollama_host = ollama_host
        self.model = model
        self.callback = callback
        self._setup_tesseract()
        self._setup_pywinauto()

    def _setup_pywinauto(self):
        try:
            from pywinauto import Desktop
            self._Desktop = Desktop
            self._has_pywinauto = True
        except ImportError:
            self._has_pywinauto = False
            log.warning("pywinauto not available — icon mode disabled, falling back to OCR")

    def _setup_tesseract(self):
        try:
            import pytesseract
            for path in TESSERACT_PATHS:
                if os.path.exists(path):
                    pytesseract.pytesseract.tesseract_cmd = path
                    break
            self._pytesseract = pytesseract
            self._has_ocr = True
        except ImportError:
            self._has_ocr = False
            log.warning("pytesseract not available — OCR disabled, falling back to vision only")

    def find_and_capture(self, query: str, word_search: bool = False):
        """
        word_search=True  → skip icon/pywinauto entirely; go straight to
                            tiled multi-pass OCR. Use this for "find the word X".
        word_search=False → normal routing: icon mode first for known apps,
                            then tiled OCR, then vision fallback.
        """
        import mss
        with mss.mss() as sct:
            monitor = sct.monitors[1]
            raw = sct.grab(monitor)
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        sw, sh = img.size
        log.info("Screen size: %dx%d, searching for %r (word_search=%s)", sw, sh, query, word_search)

        is_app_query = self._is_app_or_icon_query(query) and not word_search

        # ── Strategy 1a: Icon mode (pywinauto Shell API) ──────────────────────
        if is_app_query and self._has_pywinauto:
            result = self._find_via_icon_mode(query, sw, sh)
            if result.get("found"):
                log.info("Icon mode found %r at (%d, %d)", query, result["x"], result["y"])
                if self.callback:
                    self.callback(result)
                return result, (sw, sh)
            log.info("Icon mode found nothing for %r — falling back to OCR", query)

        # ── Strategy 1b: Tiled multi-pass OCR ────────────────────────────────
        result = self._find_via_word_mode(img, query, sw, sh)
        if result.get("found"):
            log.info("OCR found %r at (%d, %d)", query, result["x"], result["y"])
            if self.callback:
                self.callback(result)
            return result, (sw, sh)

        # ── Strategy 2: Vision model (last resort) ────────────────────────────
        log.info("OCR found nothing for %r — trying vision model", query)
        result = self._find_via_vision(img, query, sw, sh)
        if result.get("found"):
            if self.callback:
                self.callback(result)
            return result, (sw, sh)

        log.info("All strategies failed for %r", query)
        return {"found": False, "reason": f"'{query}' not found on screen"}, (sw, sh)

    def _is_app_or_icon_query(self, query: str) -> bool:
        q = query.lower().strip()
        for key, aliases in APP_ALIASES.items():
            if q in key or key in q or any(q in a or a in q for a in aliases):
                return True
        return False

    # ── Icon mode (pywinauto Shell API) ─────────────────────────────────────

    def _get_desktop_icons(self) -> list:
        icons = []
        desktop = self._Desktop(backend="win32")
        listview = None

        try:
            progman = desktop.window(class_name="Progman")
            lv = progman.child_window(class_name="SysListView32")
            if lv.exists():
                listview = lv
        except Exception as e:
            log.info("Progman path failed: %s", e)

        if listview is None:
            try:
                for w in desktop.windows(class_name="WorkerW"):
                    try:
                        lv = w.child_window(class_name="SysListView32")
                        if lv.exists():
                            listview = lv
                            break
                    except Exception:
                        continue
            except Exception as e:
                log.info("WorkerW path failed: %s", e)

        if listview is None:
            log.info("SysListView32 not found on desktop.")
            return []

        try:
            count = listview.item_count()
        except Exception as e:
            log.warning("Failed to get item_count: %s", e)
            return []

        for idx in range(count):
            try:
                item = listview.get_item(idx)
                name = item.text()
                rect = item.rectangle()
                x, y = rect.left, rect.top
                w, h = rect.right - rect.left, rect.bottom - rect.top
                icons.append({
                    "name": name, "x": x, "y": y, "w": w, "h": h,
                    "cx": x + w // 2, "cy": y + h // 2,
                })
            except Exception as e:
                log.info("Skipping item %d: %s", idx, e)
                continue

        return icons

    def _find_via_icon_mode(self, query: str, sw: int, sh: int) -> dict:
        try:
            icons = self._get_desktop_icons()
        except Exception as e:
            log.warning("Icon mode failed: %s", e)
            return {"found": False}

        aliases = self._get_aliases(query.lower().strip())
        for icon in icons:
            name_l = icon["name"].lower()
            for alias in aliases:
                if alias in name_l or name_l in alias:
                    return {
                        "found": True,
                        "x": icon["cx"],
                        "y": icon["cy"],
                        "label": icon["name"],
                        "confidence": 1.0,
                        "method": "icon_mode",
                    }
        return {"found": False}

    # ── Word mode (tiled multi-pass OCR + NMS) ──────────────────────────────

    def _preprocess_tile(self, img_bgr, pass_name: str):
        import cv2
        if pass_name == "a_2x_grey":
            grey = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
            return cv2.resize(grey, (0, 0), fx=2.0, fy=2.0, interpolation=cv2.INTER_LANCZOS4)
        elif pass_name == "c_clahe_2x":
            grey = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            eq = clahe.apply(grey)
            return cv2.resize(eq, (0, 0), fx=2.0, fy=2.0, interpolation=cv2.INTER_LANCZOS4)
        elif pass_name == "d_thresh":
            grey = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
            _, thresh = cv2.threshold(grey, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            return cv2.resize(thresh, (0, 0), fx=2.0, fy=2.0, interpolation=cv2.INTER_LANCZOS4)
        raise ValueError(f"Unknown pass: {pass_name}")

    def _ocr_tile(self, args):
        tile_bgr, tx, ty, scale, pass_name = args
        processed = self._preprocess_tile(tile_bgr, pass_name)
        try:
            data = self._pytesseract.image_to_data(
                processed, config="--psm 6 --oem 3",
                output_type=self._pytesseract.Output.DICT,
            )
        except Exception as e:
            # [FIXED - LOW] was silent bare except; now logs for traceability
            log.debug("_ocr_tile failed on pass %s: %s", pass_name, e)
            return []

        results = []
        for i, text in enumerate(data["text"]):
            text = text.strip()
            if not text:
                continue
            try:
                conf = int(data["conf"][i])
            except (ValueError, TypeError):
                conf = 0
            if conf < CONF_THRESHOLD:
                continue
            x = tx + int(data["left"][i] / scale)
            y = ty + int(data["top"][i] / scale)
            w = int(data["width"][i] / scale)
            h = int(data["height"][i] / scale)
            if w < 4 or h < 4:
                continue
            results.append({"text": text, "conf": conf, "x": x, "y": y, "w": w, "h": h})
        return results

    @staticmethod
    def _iou(a, b):
        ax1, ay1 = a["x"], a["y"]
        ax2, ay2 = ax1 + a["w"], ay1 + a["h"]
        bx1, by1 = b["x"], b["y"]
        bx2, by2 = bx1 + b["w"], by1 + b["h"]
        inter_w = max(0, min(ax2, bx2) - max(ax1, bx1))
        inter_h = max(0, min(ay2, by2) - max(ay1, by1))
        inter = inter_w * inter_h
        union = a["w"] * a["h"] + b["w"] * b["h"] - inter
        return inter / union if union > 0 else 0.0

    def _nms(self, detections):
        dets = sorted(detections, key=lambda d: d["conf"], reverse=True)
        kept, suppressed = [], set()
        for i, d in enumerate(dets):
            if i in suppressed:
                continue
            kept.append(d)
            for j in range(i + 1, len(dets)):
                if j not in suppressed and self._iou(d, dets[j]) >= NMS_IOU_THRESHOLD:
                    suppressed.add(j)
        return kept

    def _find_via_word_mode(self, img: Image.Image, query: str, sw: int, sh: int) -> dict:
        """Tiled multi-pass OCR + NMS, then match the query against survivors."""
        if not self._has_ocr:
            return {"found": False, "reason": "pytesseract not available"}
        import cv2
        import numpy as np
        t0 = time.perf_counter()
        screenshot_bgr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)

        tiles = []
        y = 0
        while y < sh:
            x = 0
            while x < sw:
                tile = screenshot_bgr[y:y + TILE_H, x:x + TILE_W]
                for pass_name, scale in WORD_PASSES:
                    tiles.append((tile.copy(), x, y, scale, pass_name))
                x += (TILE_W - OVERLAP_X)
            y += (TILE_H - OVERLAP_Y)

        all_dets = []
        with ThreadPoolExecutor(max_workers=TILE_WORKERS) as ex:
            futs = [ex.submit(self._ocr_tile, t) for t in tiles]
            for fut in as_completed(futs):
                # [FIXED - MEDIUM] was unguarded; a worker exception would abort
                # the entire scan mid-pass with no log entry
                try:
                    all_dets.extend(fut.result())
                except Exception as e:
                    log.warning("OCR tile worker failed: %s", e)
                    continue

        final = self._nms(all_dets)
        log.info(
            "Word mode: %d raw -> %d after NMS (%.2fs)",
            len(all_dets), len(final), time.perf_counter() - t0,
        )

        q = query.lower().strip()
        q_words = q.split()
        best, best_conf = None, 0

        for d in final:
            text_l = d["text"].lower().strip()
            if not text_l:
                continue

            score = 0
            if q in text_l:
                score = 3
            elif text_l in q:
                score = 2
            elif any(w in text_l for w in q_words if len(w) > 2):
                score = 1

            if score > 0:
                weighted = d["conf"] * score
                if weighted > best_conf:
                    best_conf = weighted
                    best = {
                        "found": True,
                        "x": d["x"] + d["w"] // 2,
                        "y": d["y"] + d["h"] // 2,
                        "label": d["text"],
                        "confidence": d["conf"] / 100.0,
                        "method": "word_mode",
                    }

        return best if best else {"found": False, "reason": f"'{query}' not visible via word mode"}

    # ── Vision fallback ───────────────────────────────────────────────────────

    def _find_via_vision(self, img: Image.Image, query: str, sw: int, sh: int) -> dict:
        """Try vision model on a few targeted crops before giving up."""
        crops = [
            ("desktop_strip", img.crop((0, 0, 200, sh)),      0,        0,        1.0),
            ("taskbar",       img.crop((0, sh - 80, sw, sh)), 0,        sh - 80,  1.0),
            ("fullscreen",    img,                             0,        0,        0.4),
        ]
        for label, region, ox, oy, max_scale in crops:
            result = self._vision_query(region, query,
                                        offset_x=ox, offset_y=oy,
                                        max_scale=max_scale, sw=sw, sh=sh)
            if result.get("found"):
                log.info("Vision (%s) found %r at (%d, %d)", label, query, result["x"], result["y"])
                return result
        return {"found": False}

    def _vision_query(self, region: Image.Image, query: str,
                      offset_x: int, offset_y: int,
                      max_scale: float, sw: int, sh: int) -> dict:
        try:
            rw, rh = region.size
            scale = min(max_scale, 800 / max(rw, rh))
            tw, th = max(1, int(rw * scale)), max(1, int(rh * scale))
            thumb = region.resize((tw, th), Image.LANCZOS)

            buf = io.BytesIO()
            thumb.save(buf, format="JPEG", quality=75)
            b64 = base64.b64encode(buf.getvalue()).decode()

            # [FIXED - MEDIUM] cap query to avoid bloating moondream's small context
            safe_query = query[:120]
            prompt = (
                f"Locate the center of the '{safe_query}' icon or item in this image. "
                f"Respond with the coordinates inside square brackets like [X, Y]. "
                f"If the item is completely missing, respond with [0, 0]."
            )

            resp = requests.post(
                f"{self.ollama_host}/api/chat",
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "user", "content": prompt, "images": [b64]}
                    ],
                    "stream": False,
                    "options": {"temperature": 0.4},
                },
                timeout=20,
            )
            resp.raise_for_status()
            text = resp.json().get("message", {}).get("content", "").strip()
            log.info("Vision response for %r: %r", query, text)

            bracket_match = re.search(r"[\[\(](\d+)[,\s]+(\d+)[\]\)]", text)
            if bracket_match:
                vx, vy = int(bracket_match.group(1)), int(bracket_match.group(2))
            else:
                nums = re.findall(r"\b\d{2,}\b", text)
                if len(nums) >= 2:
                    vx, vy = int(nums[0]), int(nums[1])
                else:
                    return {"found": False}

            if vx == 0 and vy == 0:
                return {"found": False}

            ax = offset_x + int(vx / scale)
            ay = offset_y + int(vy / scale)

            if 0 < ax <= sw and 0 < ay <= sh:
                return {
                    "found": True,
                    "x": ax,
                    "y": ay,
                    "label": query,
                    "confidence": 0.8,
                    "method": "vision",
                }
        except Exception as e:
            log.warning("Vision query failed: %s", e)

        return {"found": False}

    # ── OCR core ──────────────────────────────────────────────────────────────

    def _ocr_region(self, img: Image.Image, query: str,
                    offset_x=0, offset_y=0,
                    exclude_terminal=False,
                    exclude_right=0) -> dict:
        if not self._has_ocr:
            return {"found": False, "reason": "pytesseract not available"}
        try:
            import cv2
            import numpy as np
            ocr_scale = 2.0
            img_arr = np.array(img)
            grey = cv2.cvtColor(img_arr, cv2.COLOR_RGB2GRAY)
            scaled = cv2.resize(
                grey, (0, 0), fx=ocr_scale, fy=ocr_scale,
                interpolation=cv2.INTER_LANCZOS4,
            )
            data = self._pytesseract.image_to_data(
                scaled, output_type=self._pytesseract.Output.DICT,
                config="--psm 11 --oem 3"
            )
        except Exception as e:
            log.warning("OCR failed: %s", e)
            return {"found": False, "reason": str(e)}

        aliases = self._get_aliases(query.lower().strip())
        sw = img.width
        n = len(data["text"])
        best = None
        best_conf = 0

        for i in range(n):
            word = data["text"][i].strip().lower()
            if not word or len(word) < 2 or int(data["conf"][i]) < 60:
                continue
            wx = data["left"][i]
            if exclude_right and (wx / ocr_scale) > (sw - exclude_right):
                continue
            if exclude_terminal and any(noise in word for noise in TERMINAL_NOISE):
                continue
            for alias in aliases:
                first = alias.split()[0]
                if first in word or word in first:
                    conf = int(data["conf"][i])
                    if conf > best_conf:
                        best_conf = conf
                        best = {
                            "found": True,
                            "x": offset_x + int((wx + data["width"][i] / 2) / ocr_scale),
                            "y": offset_y + int((data["top"][i] + data["height"][i] / 2) / ocr_scale),
                            "label": data["text"][i].strip(),
                            "confidence": conf / 100.0,
                            "method": "ocr",
                        }

        return best if best else {"found": False, "reason": f"'{query}' not visible via OCR"}

    def _get_aliases(self, query: str) -> list:
        terms = {query}
        for w in query.split():
            if len(w) > 2:
                terms.add(w)
        for key, aliases in APP_ALIASES.items():
            if query in key or key in query or \
               any(query in a or a in query for a in aliases):
                terms.update(aliases)
                terms.add(key)
        return list(terms)
