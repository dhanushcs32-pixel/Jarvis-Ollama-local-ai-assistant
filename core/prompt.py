"""
Prompt builders for the vision assistant.

Moondream gets a simple direct question — it cannot follow JSON schemas
or system prompts. All other models get the full structured JSON prompt.
"""

from typing import Optional

MOONDREAM_MODELS = ("moondream",)

# ── Moondream prompt ──────────────────────────────────────────────────────────

MOONDREAM_QUESTION = (
    "What application is open on this screen? "
    "What text or content is visible? "
    "What is the user currently doing?"
)

# ── Standard instruction-tuned model prompt ───────────────────────────────────

SYSTEM_PROMPT = """You are a vision-enabled AI assistant running locally on the user's Windows PC.
You can see the user's screen and execute actions on their behalf.

ALWAYS respond with valid JSON matching this exact schema:
{{
  "description": "<1-2 sentences describing what you see or what you did>",
  "actions": [
    {{
      "action": "<action_name>",
      "<action-specific fields>": "<values>"
    }}
  ]
}}

Available actions and their fields:

click        : {{"action": "click", "x": int, "y": int, "button": "left"|"right"|"double"}}
type         : {{"action": "type", "text": "<string to type>"}}
press_key    : {{"action": "press_key", "key": "<key name, e.g. enter, ctrl+c>"}}
scroll       : {{"action": "scroll", "x": int, "y": int, "amount": int}}
move_mouse   : {{"action": "move_mouse", "x": int, "y": int}}
open_app     : {{"action": "open_app", "name": "<app name or path>"}}
search_file  : {{"action": "search_file", "query": "<filename or content>"}}
speak        : {{"action": "speak", "text": "<text to say aloud>"}}
describe     : {{"action": "describe"}}

Rules:
- Use SCREEN COORDINATES directly from what you see (the image is {W}x{H} pixels).
- If you cannot complete a task safely, use "describe" and explain why.
- Keep description concise. The user hears it via TTS.
- Return only JSON. No markdown, no prose outside the JSON object.
"""


def _is_moondream(cfg) -> bool:
    return cfg.model.lower().startswith(MOONDREAM_MODELS)


def build_system_prompt(cfg) -> str:
    if _is_moondream(cfg):
        return ""  # moondream ignores system prompts
    return SYSTEM_PROMPT.format(W=cfg.capture_width, H=cfg.capture_height)


def build_user_message(
    image_b64: str,
    user_query: Optional[str],
    active_window: Optional[str],
    cfg=None,
) -> dict:
    if cfg and _is_moondream(cfg):
        return _build_moondream_message(image_b64, user_query, active_window)
    return _build_standard_message(image_b64, user_query, active_window)


def _build_moondream_message(
    image_b64: str,
    user_query: Optional[str],
    active_window: Optional[str],
) -> dict:
    """
    Moondream works best with a single, direct question.
    If the user gave a voice command, use that as the question directly.
    Otherwise fall back to the default screen-description question.
    """
    if user_query:
        # [FIXED - MEDIUM] cap query to avoid pushing past moondream's small context
        q = user_query.strip()[:300]
        if not q.endswith("?"):
            q += "?"
        prompt = q
    else:
        prompt = MOONDREAM_QUESTION

    return {
        "role": "user",
        "content": prompt,
        "images": [image_b64],
    }


def _build_standard_message(
    image_b64: str,
    user_query: Optional[str],
    active_window: Optional[str],
) -> dict:
    parts = []
    if active_window:
        # [FIXED - MEDIUM] sanitise window title — strip control chars and cap length.
        # Browser tabs can have full URLs as titles; control chars from some apps
        # can corrupt the prompt string.
        active_window = active_window[:100].replace("\n", " ").replace("\r", "")
        parts.append(f"Active window: {active_window}")
    if user_query:
        parts.append(f"User request: {user_query}")
    else:
        parts.append(
            "Analyze the screen and describe what you see. "
            "If there is an obvious task to complete, do it."
        )

    return {
        "role": "user",
        "content": "\n".join(parts),
        "images": [image_b64],
    }
