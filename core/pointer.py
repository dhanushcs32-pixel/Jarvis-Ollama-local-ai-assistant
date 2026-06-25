"""
Pointer — handles "point to X" / "where is X" / "find the word X" voice commands.
Uses IconFinder OCR to locate the target, then fires a clean
Clicky-style overlay: pulsing circle + arrow, no cluttered panel.

FIXES (Session 27):
  - Added POINT_TRIGGERS for "search for (the word/text) X on screen",
    "find the word X", "find the text X", "look for X on screen",
    "where is the word X", "search X on screen".
"""

import logging
import re

log = logging.getLogger(__name__)

# Patterns where "word"/"text" is explicit → word_search=True (skip icon mode)
WORD_SEARCH_TRIGGERS = [
    r"search for (?:the )?(?:word |text )(.+?) on (?:(?:my|the) )?(?:screen|desktop|display|monitor)",
    r"search for (?:the )?(?:word |text )(.+)",
    r"find (?:the )?(?:word |text )(.+)",
    r"look for (?:the )?(?:word |text )(.+?) on (?:(?:my|the) )?(?:screen|desktop|display|monitor)",
    r"where is (?:the )?(?:word |text )(.+)",
]

# General pointer patterns (icon mode allowed)
POINT_TRIGGERS = [
    r"point (?:me )?to (.+)",
    r"point to (.+)",
    r"where(?:'s| is) (.+)",
    r"find (.+?) on (?:the )?screen",
    r"show me (?:where )?(.+?) is",
    r"locate (.+)",
    r"highlight (.+)",
    r"search for (?:the )?(?:word |text )?(.+?) on (?:(?:my|the) )?(?:screen|desktop|display|monitor)",
    r"search for (?:the )?(?:word |text )(.+)",
    r"find (?:the )?(?:word |text )(.+)",
    r"look for (.+?) on (?:(?:my|the) )?(?:screen|desktop|display|monitor)",
    r"where is (?:the )?(?:word |text )(.+)",
]

_LOCATION_SUFFIX = re.compile(
    r"\s+(?:on|in|at|for|from)\s+(?:(?:my|the)\s+)?(?:screen|desktop|display|monitor|computer|pc)$",
    re.IGNORECASE,
)

def extract_target(query: str) -> str | None:
    q = query.lower().strip().rstrip("?.,!")
    q = re.sub(r",\s*", " ", q)
    q = re.sub(r"\s+", " ", q).strip()
    for pattern in POINT_TRIGGERS:
        m = re.search(pattern, q)
        if m:
            target = m.group(1).strip().rstrip("?.,!")
            target = _LOCATION_SUFFIX.sub("", target).strip()
            return target if target else None
    return None


def is_word_search(query: str) -> bool:
    q = query.lower().strip().rstrip("?.,!")
    q = re.sub(r",\s*", " ", q)
    q = re.sub(r"\s+", " ", q).strip()
    for pattern in WORD_SEARCH_TRIGGERS:
        if re.search(pattern, q):
            return True
    return False


def is_point_command(query: str) -> bool:
    return extract_target(query) is not None


def handle(query: str, bridge, speak_fn, screen_size: tuple, cfg=None) -> bool:
    target = extract_target(query)
    if not target:
        return False

    log.info("Pointer: looking for %r", target)
    # NOTE: speak_fn is called before IconFinder initialises — this is intentional.
    # It gives the user immediate audio feedback even before the search begins.
    # If IconFinder.__init__ raises, the user will hear both this message and the
    # error message below; that ordering is acceptable and expected.
    speak_fn(f"Looking for {target}.")

    try:
        from core.icon_finder import IconFinder

        def _on_result(result: dict):
            """Forward icon coords to the Qt overlay as soon as found."""
            if result.get("found"):
                x, y = result["x"], result["y"]
                label = result.get("label", target)
                sw, sh = screen_size
                bridge.on_annotate.emit({
                    "annotations": [
                        {"type": "arrow",  "x1": sw // 2, "y1": sh // 2,
                         "x2": x, "y2": y, "label": ""},
                        {"type": "circle", "x": x, "y": y, "r": 50, "label": label},
                    ],
                    "instructions": [],
                    "pointer_mode": True,
                })

        ollama_host = cfg.ollama_host if cfg else "http://localhost:11434"
        word_search = is_word_search(query)
        finder = IconFinder(ollama_host=ollama_host, callback=_on_result)
        result, actual_size = finder.find_and_capture(target, word_search=word_search)
    except Exception as e:
        log.error("IconFinder error: %s", e)
        speak_fn(f"Sorry, I had an error searching for {target}.")
        return True

    if not result.get("found"):
        speak_fn(f"I couldn't find {target} on your screen.")
        log.info("Pointer: %r not found", target)
        return True

    x, y = result["x"], result["y"]
    log.info("Pointer: %r found at (%d, %d)", target, x, y)
    speak_fn(f"Found {target}.")
    return True
