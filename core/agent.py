"""
Agent -- central coordinator with dual-model routing.

  reasoning_model  -> DEFAULT permanent channel for all chat conversations, reasoning, and task planning.
                      Configured via config.json "reasoning_model" key.
  vision_model     -> ONLY used as a temporary on-demand vision utility to describe screen states or find icons.
                      Configured via config.json "vision_model" key.

File Guard Feature:
  Interceptors added to strictly read, write, clear, or overwrite ONE file only:
  ~/Desktop/To-Do.txt  (resolves to the user's Desktop on Windows, macOS, and Linux)
"""

import json
import os
import re
import time
import logging
from typing import Optional

from core.config import Config
from core.capture import ScreenCapture
from core.memory import Memory
from core.ollama_client import OllamaClient
from core.prompt import build_user_message
from actions.dispatcher import ActionDispatcher

log = logging.getLogger(__name__)

# ── Intent classifiers ────────────────────────────────────────────────────

SCREEN_TRIGGERS = re.compile(
    r"\b(screen|open|click|type|scroll|find|show|what.s on|"
    r"describe|launch|close|switch|window|desktop|taskbar|maximize|minimize|"
    r"point|where is|locate|search|google|look up|navigate|browse)\b", re.IGNORECASE
)

# Queries that are inherently visual — always need Moondream, skip Qwen-first planning
VISION_TRIGGERS = re.compile(
    r"\b(describe|what.s on|what is on|what do you see|what can you see|"
    r"wallpaper|background|screenshot|screen look|look at|what.s happening|"
    r"what is happening|show me|tell me what|read (the\s+)?screen|"
    r"read (the\s+)?display)\b", re.IGNORECASE
)

# Robust, bound-checked system command expressions
VOLUME_UP_RE    = re.compile(r"\b((volume|sound).{0,15}(up|louder|increase|raise|higher)|(up|louder|increase|raise|higher).{0,15}(volume|sound))\b", re.I)
VOLUME_DOWN_RE  = re.compile(r"\b((volume|sound).{0,15}(down|lower|decrease|quieter|reduce|softer)|(down|lower|decrease|quieter|reduce|softer).{0,15}(volume|sound))\b", re.I)
VOLUME_MUTE_RE  = re.compile(r"\b(mute|unmute|silence)\b", re.I)
BRIGHT_UP_RE    = re.compile(r"\b((brightness|screen).{0,15}(up|increase|raise|brighter|higher)|(up|increase|raise|brighter|higher).{0,15}(brightness|screen))\b", re.I)
BRIGHT_DOWN_RE  = re.compile(r"\b((brightness|screen).{0,15}(down|decrease|lower|dimmer|dim|reduce)|(down|decrease|lower|dimmer|dim|reduce).{0,15}(brightness|screen))\b", re.I)

# Explicit target regex expressions for file interactions
TODO_ADD_RE       = re.compile(r"\b((add|note|remind).{0,20}(todo|to do|to-do|list|task))\b", re.I)
TODO_ALT_RE       = re.compile(r"\b((todo|to.do).{0,10}(add|note|write|put))\b", re.I)
TODO_READ_RE      = re.compile(r"\b((read|show|what.s on|check).{0,10}(todo|to.do|list|tasks))\b", re.I)
TODO_CLEAR_RE     = re.compile(r"\b((clear|delete|wipe|empty).{0,10}(todo|to.do|list))\b", re.I)
TODO_OVERWRITE_RE = re.compile(r"\b(overwrite|replace).{0,15}(todo|to.do|list)\b", re.I)

# IMPORTANT: Only matches genuine send-intent phrases.
# Bare "open whatsapp" must NOT match — the open-whatsapp guard below runs first.
WHATSAPP_RE     = re.compile(
    r"(?:"
    # "send hi to bob on whatsapp" / "message john on whatsapp"
    r"\b(?:send|sent|message|msg|text)\b.{0,30}\b(?:whatsapp|whats\s*app)\b"
    r"|"
    # "whatsapp send hi to bob" / "whatsapp message john saying..."
    r"\b(?:whatsapp|whats\s*app)\b.{0,30}\b(?:send|sent|message|msg|saying|tell)\b"
    r"|"
    # "in/on/via whatsapp send hi to bob"  (STT: "and whatsapp send ...")
    r"\b(?:in|on|via|and)\s+(?:whatsapp|whats\s*app)\b.{0,30}\b(?:send|sent|message|msg|hi|hello|hey)\b"
    r"|"
    # "send hi to bob in whatsapp"
    r"\b(?:send|sent|message|msg|text)\b.{0,30}\b(?:in|on|via)\s+(?:whatsapp|whats\s*app)\b"
    r")", re.I)
CONFIRM_RE      = re.compile(
    r"\b(confirm|yes\s+send|send\s+it|go\s+ahead"
    r"|send\s+(the\s+)?message"
    r"|sent\s+(the\s+)?message"
    r"|message\s+sent"
    r"|send\s+now"
    r"|just\s+send"
    r"|do\s+it"
    r"|yeah|yep|yup"
    r")\b", re.I)
CANCEL_RE       = re.compile(
    r"\b(cancel(?:led|ed)?|don.t send|abort|stop sending|stop that"
    r"|cancel\s+(the\s+)?message|discard\s+(the\s+)?message"
    r"|never\s+mind|forget\s+it|skip\s+it)\b", re.I)
OPEN_CHROME_RE  = re.compile(r"\b(open|launch).{0,10}(chrome|browser|google)\b", re.I)
OPEN_URL_RE     = re.compile(r"\b(go to|open|navigate).{0,10}(https?://|www\.|\.com|\.in|\.org)\b", re.I)


def _extract_url(text: str) -> str:
    m = re.search(r"(https?://\S+|www\.\S+|\S+\.(com|in|org|net|io|co)\S*)", text, re.I)
    return m.group(0) if m else ""


def _extract_todo_item(text: str) -> str:
    m = re.search(r"\badd\b(.+?)\b(to|in|on)\b.{0,10}(todo|list|tasks?)\b", text, re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"\b(remind me to|note|remember)\b(.+)", text, re.I)
    if m:
        return m.group(2).strip()
    return text


def _extract_whatsapp(text: str) -> tuple:
    # Strip STT noise: "and whatsapp", "in whatsapp", "on whatsapp", "via whatsapp"
    # Also strip trailing/leading wake-word fragments that may survive
    cleaned = re.sub(r"\b(and|in|on|via|through)\s+(whatsapp|whats\s*app)\b", "", text, flags=re.I)
    cleaned = re.sub(r"\b(whatsapp|whats\s*app)\b", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()

    # Pattern 1: "send <msg> to <contact>"  e.g. "send hi to aditya"
    m = re.search(r"\b(?:send|sent|message|msg|text)\s+(.+?)\s+to\s+([\w][\w\s]*?)(?:\s*$)", cleaned, re.I)
    if m:
        return m.group(2).strip(), m.group(1).strip()

    # Pattern 2: "to <contact> say/saying/that <msg>"
    m = re.search(r"\bto\s+([\w][\w\s]*?)\s+(?:say|saying|message|that|:)\s+(.+)", cleaned, re.I)
    if m:
        return m.group(1).strip(), m.group(2).strip()

    # Pattern 3: "<msg> to <contact>"  fallback — last resort
    m = re.search(r"(.+?)\s+to\s+([\w][\w\s]+)$", cleaned, re.I)
    if m:
        msg_part = m.group(1).strip()
        # strip leading send/sent/message verb if present
        msg_part = re.sub(r"^(?:send|sent|message|msg|text)\s+", "", msg_part, flags=re.I).strip()
        return m.group(2).strip(), msg_part

    return "", cleaned


def is_chat_query(query: str) -> bool:
    if not query:
        return False
    q = query.strip().lower()
    if SCREEN_TRIGGERS.search(q):
        return False
    return True


class Agent:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.capture = ScreenCapture(cfg)
        self.memory = Memory(cfg)
        self.ollama = OllamaClient(cfg)
        self.dispatcher = ActionDispatcher(cfg)

    def _get_secure_todo_path(self) -> str:
        """Returns ~/Desktop/To-Do.txt — works on Windows, macOS, and Linux."""
        return os.path.join(os.path.expanduser("~"), "Desktop", "To-Do.txt")

    def _extract_amount(self, query: str, default: int) -> int:
        match = re.search(r"\b\d+\b", query)
        if match:
            # [FIXED - MEDIUM] clamp to prevent e.g. "volume up 9999" hanging UI
            return min(int(match.group(0)), 50)
        return default

    def is_screen_query(self, query: str) -> bool:
        """Returns True if the query needs screen access (should use run_query)."""
        q = query.strip().lower()
        return bool(SCREEN_TRIGGERS.search(q) or VISION_TRIGGERS.search(q))

    def chat(self, user_query: str) -> dict:
        t0 = time.monotonic()
        log.info("Chat mode: %r", user_query)

        result = self._handle_system_command(user_query.strip())
        if result is not None:
            log.info("System command intercepted in chat() — bypassing Qwen")
            return result

        history = self.memory.get_recent()

        response = self.ollama.chat_text(user_query, history)

        log.info("Chat completed in %.2fs", time.monotonic() - t0)
        self.dispatcher.speak(response)
        self.memory.add_turn(user=user_query, assistant=response)
        return {"description": response, "actions": []}

    def analyze_screen(self, user_query: Optional[str] = None) -> dict:
        t0 = time.monotonic()
        q = (user_query or "").strip()

        # 1. System commands always bypass both models entirely
        if q:
            result = self._handle_system_command(q)
            if result is not None:
                return result

        # 2. Pure chat query (no screen triggers) → Qwen only, never Moondream
        if q and is_chat_query(q):
            return self.chat(q)

        # 3a. Inherently visual query (describe/wallpaper/what's on screen) →
        #     skip Qwen-first planning, go straight to Moondream
        if q and VISION_TRIGGERS.search(q):
            log.info("Vision query detected — going straight to Moondream")
            # fall through to Moondream block below (skip step 3b)

        # 3b. Screen-related but action-plannable query → Qwen first
        elif q:
            log.info("Screen query detected — asking Qwen to plan without vision first")
            t1 = time.monotonic()
            raw_plan = self.ollama.action_plan(q, "")
            log.info("Qwen action plan generated in %.2fs", time.monotonic() - t1)
            parsed = self._parse_response(raw_plan, fallback_desc="")

            # Decide if Moondream is actually needed:
            # Moondream is required when Qwen's plan contains actions that
            # need pixel-level coordinates (click, point, scroll) but no
            # coords were provided, or when Qwen explicitly signals it.
            needs_vision = parsed.get("need_screen") or any(
                a.get("action") in ("click", "point", "scroll", "find_icon")
                and not (a.get("x") or a.get("target"))
                for a in parsed.get("actions", [])
            )

            if not needs_vision:
                log.info("Qwen resolved query without vision — skipping Moondream")
                self._post_plan(q, parsed, t0)
                return parsed

            log.info("Qwen needs visual context — opening temporary Moondream channel")

        # 4. Moondream: only reached when vision is genuinely needed
        log.info("Visual context needed — invoking Moondream...")
        image_b64 = self.capture.grab_base64(max_width=960)

        # [FIXED - Session 36] Was building a compound, multi-clause prompt
        # inline here ("Look at this screenshot. The user wants to: '{q}'. "
        # "Describe only what's relevant... List any visible apps...{exclude}").
        # core/prompt.py's own docstring documents that Moondream "cannot
        # follow JSON schemas or system prompts" and needs "a simple direct
        # question" — build_user_message()/_build_moondream_message() exist
        # for exactly this, but analyze_screen() was never routed through
        # them. The compound prompt is the likely cause of Moondream
        # returning empty completions (see WARNING log added above).
        active_window = None
        try:
            active_window = self.capture.get_active_window_title()
        except Exception:
            pass  # best-effort context only; never block on this

        user_msg = build_user_message(
            image_b64=image_b64,
            user_query=q if q else None,
            active_window=active_window,
            cfg=self.cfg,
        )

        t1 = time.monotonic()
        screen_desc = self.ollama.chat("", [], user_msg)
        log.info("Moondream done (%.2fs) — handing context back to Qwen", time.monotonic() - t1)

        # For pure vision/describe queries, Moondream's answer IS the response —
        # no need to run action_plan (which expects screen context + a task to plan).
        if q and VISION_TRIGGERS.search(q):
            if not screen_desc.strip():
                # [FIXED] Moondream can return an empty completion with no
                # exception/timeout — previously this meant _post_plan's
                # `if parsed.get("description"):` guard skipped speak()
                # entirely, so the assistant failed completely silently.
                screen_desc = "I couldn't make out anything useful on the screen — could you try again?"
                log.warning("Moondream returned an empty description for query: %r", q)
            parsed = {"description": screen_desc, "actions": []}
            self._post_plan(q, parsed, t0, screen_desc=screen_desc)
            return parsed

        parsed = {"description": screen_desc, "actions": []}
        if q:
            t2 = time.monotonic()
            raw_plan = self.ollama.action_plan(q, screen_desc)
            log.info("Qwen action plan generated in %.2fs", time.monotonic() - t2)
            parsed = self._parse_response(raw_plan, fallback_desc=screen_desc)

        self._post_plan(q, parsed, t0, screen_desc=screen_desc)
        return parsed

    def _post_plan(self, q: str, parsed: dict, t0: float, screen_desc: str = "") -> None:
        """Shared post-planning logic: web-action guard, speak, dispatch, memory."""

        # ── Fallback guard: block unsolicited open_url from Qwen ────────────
        if q:
            user_asked_for_web = bool(
                OPEN_URL_RE.search(q) or OPEN_CHROME_RE.search(q)
                or re.search(r"\b(search|google|look up|browse|website|webpage)\b", q, re.I)
            )
            rejected_web_actions = [
                a for a in parsed.get("actions", [])
                if a.get("action") == "open_url" and not user_asked_for_web
            ]
            if rejected_web_actions:
                log.warning(
                    "Blocked unsolicited open_url action(s) from Qwen: %s — re-prompting",
                    rejected_web_actions
                )
                suspicious_companions = [
                    a for a in parsed.get("actions", [])
                    if a.get("action") in ("type", "press_key")
                    and re.search(r"\bjarv(is)?\b", str(a.get("text", "")), re.I)
                ]
                actions_to_drop = rejected_web_actions + suspicious_companions
                parsed["actions"] = [
                    a for a in parsed.get("actions", []) if a not in actions_to_drop
                ]
                retry_query = (
                    f"{q}\n\n"
                    "(Note: do not search the web or open a browser/URL for this "
                    "request — it is about the local screen/desktop only. "
                    "Use open_app, click, or another screen action instead.)"
                )
                raw_retry = self.ollama.action_plan(retry_query, screen_desc)
                retry_parsed = self._parse_response(raw_retry, fallback_desc=screen_desc)
                retry_parsed["actions"] = [
                    a for a in retry_parsed.get("actions", []) if a.get("action") != "open_url"
                ]
                parsed["actions"].extend(retry_parsed.get("actions", []))
                if not parsed.get("description"):
                    parsed["description"] = retry_parsed.get("description", screen_desc)

        log.info("Actions: %s", [a["action"] for a in parsed.get("actions", [])])

        if parsed.get("description"):
            self.dispatcher.speak(parsed["description"])

        for action in parsed.get("actions", []):
            if action["action"] in ("describe", "speak"):
                continue
            if action["action"] not in self.cfg.allowed_actions:
                log.warning("Blocked action: %s", action["action"])
                continue
            if self.cfg.require_confirmation:
                if not self._confirm(action):
                    continue
            result = self.dispatcher.dispatch(action)

            # ── Error feedback loop ──────────────────────────────────────────
            if isinstance(result, dict) and not result.get("ok", True):
                error_msg = result.get("error", "action failed")
                log.warning(
                    "Action '%s' failed (%s) — feeding error back to Qwen for retry",
                    action.get("action"), error_msg
                )
                # [FIXED - MEDIUM] cap serialised action to avoid blowing num_ctx
                retry_query = (
                    f"{q}\n\n"
                    f"(Note: your previous attempt produced "
                    f"{json.dumps(action)[:300]} and it failed with: {error_msg}. "
                    f"Correct the JSON and try again — every open_app action "
                    f"must include a 'name' field with the exact app name, "
                    f"e.g. {{\"action\": \"open_app\", \"name\": \"Solid Edge\"}}.)"
                )
                raw_retry = self.ollama.action_plan(retry_query, screen_desc)
                retry_parsed = self._parse_response(raw_retry, fallback_desc=screen_desc)
                for retry_action in retry_parsed.get("actions", []):
                    if retry_action.get("action") not in self.cfg.allowed_actions:
                        continue
                    if retry_action.get("action") == "open_app" and not str(retry_action.get("name") or "").strip():
                        log.warning("Retry still missing 'name' — giving up on this action")
                        continue
                    self.dispatcher.dispatch(retry_action)

        self.memory.add_turn(
            user=q or "(screen analysis)",
            assistant=parsed.get("description", screen_desc)
        )
        log.info("Total pipeline process: %.2fs", time.monotonic() - t0)

    def _handle_system_command(self, q: str) -> Optional[dict]:
        # Bare affirmatives (yeah/yep/yup/do it) only count as confirms when
        # there is actually a pending WhatsApp message — otherwise fall through
        # to Qwen as normal conversation.
        _BARE_AFFIRMATIVE_RE = re.compile(r"^\s*(yeah|yep|yup|do\s+it)\s*$", re.I)
        if CONFIRM_RE.search(q):
            if _BARE_AFFIRMATIVE_RE.match(q) and not self.dispatcher.has_pending_whatsapp():
                pass  # no pending msg — let it fall through to chat
            else:
                self.dispatcher.confirm_whatsapp()
                return {"description": "Sending message.", "actions": []}
        if CANCEL_RE.search(q):
            self.dispatcher.cancel_whatsapp()
            return {"description": "Cancelled.", "actions": []}

        if VOLUME_UP_RE.search(q):
            amount = self._extract_amount(q, default=5)
            self.dispatcher.dispatch({"action": "volume_up", "amount": amount})
            return {"description": f"Volume increased by {amount} units.", "actions": []}
        if VOLUME_DOWN_RE.search(q):
            amount = self._extract_amount(q, default=5)
            self.dispatcher.dispatch({"action": "volume_down", "amount": amount})
            return {"description": f"Volume decreased by {amount} units.", "actions": []}
        if VOLUME_MUTE_RE.search(q):
            self.dispatcher.dispatch({"action": "volume_mute"})
            return {"description": "Muted.", "actions": []}

        if BRIGHT_UP_RE.search(q):
            amount = self._extract_amount(q, default=10)
            self.dispatcher.dispatch({"action": "brightness_up", "amount": amount})
            return {"description": f"Brightness increased by {amount} units.", "actions": []}
        if BRIGHT_DOWN_RE.search(q):
            amount = self._extract_amount(q, default=10)
            self.dispatcher.dispatch({"action": "brightness_down", "amount": amount})
            return {"description": f"Brightness decreased by {amount} units.", "actions": []}

        # ── EXCLUSIVE TARGET FILE I/O SUBSYSTEM ───────────────────────────
        todo_path = self._get_secure_todo_path()

        # 1. READ Operation
        if TODO_READ_RE.search(q):
            try:
                if not os.path.exists(todo_path):
                    msg = "Your to-do list file does not exist on the desktop yet."
                else:
                    with open(todo_path, "r", encoding="utf-8") as f:
                        content = f.read().strip()
                    msg = f"Here is your to-do list: {content}" if content else "Your to-do list is empty."
            except OSError as e:
                log.error("Failed to read to-do file: %s", e)
                msg = "Sorry, I couldn't read your to-do list."
            self.dispatcher.speak(msg)
            return {"description": msg, "actions": []}

        # 2. CLEAR Operation
        if TODO_CLEAR_RE.search(q):
            try:
                with open(todo_path, "w", encoding="utf-8") as f:
                    f.write("")
                msg = "I have cleared your to-do list file on the desktop."
            except OSError as e:
                log.error("Failed to clear to-do file: %s", e)
                msg = "Sorry, I couldn't clear your to-do list."
            self.dispatcher.speak(msg)
            return {"description": msg, "actions": []}

        # 3. OVERWRITE Operation
        if TODO_OVERWRITE_RE.search(q):
            content_match = re.search(r"\b(?:with|to be)\b\s+(.+)", q, re.I)
            new_content = content_match.group(1).strip() if content_match else ""
            if not new_content:
                msg = "What content should I overwrite the file with?"
            else:
                try:
                    with open(todo_path, "w", encoding="utf-8") as f:
                        f.write(new_content)
                    msg = "Overwrote your desktop to-do list file."
                except OSError as e:
                    log.error("Failed to overwrite to-do file: %s", e)
                    msg = "Sorry, I couldn't overwrite your to-do list."
            self.dispatcher.speak(msg)
            return {"description": msg, "actions": []}

        # 4. EDIT / APPEND Operation
        if TODO_ADD_RE.search(q) or TODO_ALT_RE.search(q):
            item = _extract_todo_item(q)
            try:
                mode = "a" if os.path.exists(todo_path) else "w"
                with open(todo_path, mode, encoding="utf-8") as f:
                    if mode == "a" and os.path.getsize(todo_path) > 0:
                        f.write("\n" + item)
                    else:
                        f.write(item)
                msg = f"Added {item} to your desktop to-do list."
            except OSError as e:
                log.error("Failed to write to-do file: %s", e)
                msg = "Sorry, I couldn't add that to your to-do list."
            self.dispatcher.speak(msg)
            return {"description": msg, "actions": []}

        # ── SYSTEM APP LINKS ──────────────────────────────────────────────
        # OPEN check MUST precede WHATSAPP_RE — do not reorder.
        if re.search(r"\bopen\s+whatsapp\b", q, re.I):
            self.dispatcher.dispatch({"action": "open_whatsapp"})
            return {"description": "Opening WhatsApp.", "actions": []}

        if WHATSAPP_RE.search(q):
            contact, message = _extract_whatsapp(q)
            self.dispatcher.dispatch({
                "action": "whatsapp_send",
                "contact": contact,
                "message": message,
            })
            return {"description": "WhatsApp prepared.", "actions": []}

        if OPEN_URL_RE.search(q):
            url = _extract_url(q)
            if url:
                self.dispatcher.dispatch({"action": "open_url", "url": url})
                return {"description": f"Opening {url}.", "actions": []}
        if OPEN_CHROME_RE.search(q):
            self.dispatcher.dispatch({"action": "open_url", "url": "https://google.com"})
            return {"description": "Opening Chrome.", "actions": []}

        return None

    def _parse_response(self, raw: str, fallback_desc: str = "") -> dict:
        cleaned = raw.strip()
        # [FIXED - MEDIUM] regex fence strip handles ```json, ``` json, ```JSON, etc.
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned).rstrip("`").strip()
        try:
            data = json.loads(cleaned)
            if "actions" not in data:
                data["actions"] = []
            if "description" not in data or not data["description"]:
                data["description"] = fallback_desc
            return data
        except json.JSONDecodeError:
            pass
        return {"description": fallback_desc or cleaned, "actions": []}

    def _confirm(self, action: dict) -> bool:
        # WARNING: this uses blocking input() — if called from a Qt signal handler
        # or voice thread (the normal production path) it will freeze the UI.
        # cfg.require_confirmation defaults to False; do not enable without
        # replacing this with a Qt dialog or async prompt.
        print(f"\n⚡ Action: {json.dumps(action, indent=2)}")
        answer = input("  Execute? [y/N] ").strip().lower()
        return answer == "y"
