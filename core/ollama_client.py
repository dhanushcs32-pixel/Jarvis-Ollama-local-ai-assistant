"""
OllamaClient — dual model routing.

  vision_model    → /api/generate  (vision tasks, on-demand only)
                    set via config.json "vision_model" key
  reasoning_model → /api/chat      (all chat, reasoning, action planning — default)
                    set via config.json "reasoning_model" key

chat_text()   → always uses reasoning_model (text only, no image)
chat()        → vision_model call (only invoked when screen context is needed)
action_plan() → reasoning_model; omits Screen: line when screen_description is empty
"""

import json
import logging
import requests

log = logging.getLogger(__name__)


class OllamaClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self.base_url = cfg.ollama_host.rstrip("/")
        self._session = requests.Session()
        self._available_models: set | None = None
        # 0.0 = expired sentinel — triggers a fetch on the very first use
        self._cache_expiry_ts: float = 0.0

    def _invalidate_model_cache(self):
        """Force a fresh /api/tags fetch on next availability check."""
        self._available_models = None

    @property
    def _vision_model(self) -> str:
        return self.cfg.vision_model

    @property
    def _reasoning_model(self) -> str:
        return self.cfg.reasoning_model

    def _is_moondream(self) -> bool:
        return self._vision_model.lower().startswith("moondream")

    # ── Public API ────────────────────────────────────────────────────────

    def chat(self, system: str, history: list, user_message: dict,
             stream: bool = False) -> str:
        """Vision call — always uses moondream."""
        return self._generate_moondream(user_message, stream)

    def chat_text(self, user_query: str, history: list) -> str:
        """
        Pure text conversation or reasoning — uses reasoning_model.
        Falls back to vision_model if reasoning_model unavailable.
        """
        if not self._model_available(self._reasoning_model):
            log.warning("%s not available, falling back to vision model for text.", self._reasoning_model)
            return self._moondream_text(user_query, history)
        return self._qwen_chat(user_query, history)

    def action_plan(self, user_query: str, screen_description: str) -> str:
        """
        Ask qwen to plan actions based on screen description from moondream.
        Returns JSON action plan.
        """
        if not self._model_available(self._reasoning_model):
            return "{}"

        # [FIXED - MEDIUM] cap inputs to avoid silently overflowing num_ctx=4096
        screen_description = screen_description[:1200]
        user_query = user_query[:400]

        system = (
            "You are an AI assistant that controls a Windows PC. "
            "You receive a screen description and a user request, "
            "and return a JSON action plan.\n\n"
            "Available actions and their REQUIRED fields:\n"
            '  click       — {"action": "click", "x": <int>, "y": <int>, "button": "left"|"right"|"double" (optional, default "left")}\n'
            '  type         — {"action": "type", "text": "<string>"}\n'
            '  press_key    — {"action": "press_key", "key": "<key or combo, e.g. \\"ctrl+s\\">"}\n'
            '  scroll       — {"action": "scroll", "amount": <int>}\n'
            '  open_app     — {"action": "open_app", "name": "<exact app name, e.g. \\"Solid Edge\\">"}\n'
            '  open_url     — {"action": "open_url", "url": "<full url>"}\n'
            '  volume_up/volume_down — {"action": "volume_up", "amount": <int>}\n'
            '  volume_mute  — {"action": "volume_mute"}\n'
            '  brightness_up/brightness_down — {"action": "brightness_up", "amount": <int>}\n'
            '  todo_add     — {"action": "todo_add", "item": "<string>"}\n'
            '  todo_read / todo_clear — {"action": "todo_read"}\n'
            '  todo_done    — {"action": "todo_done", "item": "<keyword>"}\n'
            '  whatsapp_send — {"action": "whatsapp_send", "contact": "<name>", "message": "<text>"}\n'
            '  speak        — {"action": "speak", "text": "<string>"}\n'
            '  describe     — {"action": "describe"}\n\n'
            "CRITICAL RULES:\n"
            "1. EVERY field marked required above MUST be present and non-empty. "
            "open_app with no 'name' is INVALID — never output {\"action\": \"open_app\"} alone.\n"
            "   Correct:   {\"action\": \"open_app\", \"name\": \"Solid Edge\"}\n"
            "   WRONG:     {\"action\": \"open_app\"}\n"
            "2. Only use open_url, or web searches, if the user's request explicitly asks to "
            "search, browse, google, or open a website. If the request is about finding, "
            "opening, or interacting with something on the local screen or desktop "
            "(an app, icon, window, button), use open_app/click/type/press_key instead — "
            "never substitute a web search for a local screen action.\n"
            "3. If a previous attempt is mentioned as having failed, fix the specific problem "
            "named in the error rather than repeating the same JSON or switching strategy "
            "to a web search.\n\n"
            "Return ONLY valid JSON: "
            '{"description": "...", "actions": [{"action": "...", ...}]}\n'
            "No markdown, no prose outside JSON."
        )
        screen_line = f"Screen: {screen_description}\n" if screen_description else ""
        prompt = (
            f"{screen_line}"
            f"User request: {user_query}\n"
            f"Return the action plan as JSON."
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user",   "content": prompt},
        ]
        payload = {
            "model": self._reasoning_model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": 0.1,
                "num_predict": 400,
                "num_ctx": 4096,
                "num_gpu": 0,
                "num_thread": 8,
            },
        }
        try:
            resp = self._session.post(
                f"{self.base_url}/api/chat",
                json=payload, timeout=(5, 60)
            )
            resp.raise_for_status()
            return resp.json()["message"]["content"]
        except Exception as e:
            log.error("action_plan error: %s", e)
            return "{}"

    # ── moondream vision ──────────────────────────────────────────────────

    def _generate_moondream(self, user_message: dict, stream: bool) -> str:
        prompt = user_message.get("content", "Describe what is on the screen.")
        images = user_message.get("images", [])
        payload = {
            "model": self._vision_model,
            "prompt": prompt,
            "images": images,
            "stream": stream,
            "options": {
                "temperature": 0.1,
                "num_predict": 300,
                "num_ctx": 2048,
                "num_gpu": 0,
                "num_thread": 8,
            },
        }
        try:
            resp = self._session.post(
                f"{self.base_url}/api/generate",
                json=payload, timeout=(5, 120), stream=stream
            )
            resp.raise_for_status()
            if stream:
                return self._collect_generate_stream(resp)
            return resp.json().get("response", "")
        except requests.exceptions.ConnectionError:
            return "Ollama is not running."
        except requests.exceptions.Timeout:
            return "Request timed out."
        except Exception as e:
            log.error("moondream error: %s", e)
            return f"Error: {e}"

    def _moondream_text(self, user_query: str, history: list) -> str:
        """Moondream fallback for text-only queries."""
        context = ""
        for msg in history[-4:]:
            role = "User" if msg["role"] == "user" else "Assistant"
            content = msg.get("content", "")
            if content and not content.startswith("(screen"):
                context += f"{role}: {content}\n"
        prompt = (
            f"You are a friendly AI assistant. Be concise and warm.\n"
            f"{context}User: {user_query}\nAssistant:"
        )
        payload = {
            "model": self._vision_model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.7, "num_predict": 200,
                        "num_ctx": 1024, "num_gpu": 0, "num_thread": 8},
        }
        try:
            resp = self._session.post(
                f"{self.base_url}/api/generate",
                json=payload, timeout=(5, 60)
            )
            resp.raise_for_status()
            return resp.json().get("response", "").strip()
        except Exception as e:
            return f"Error: {e}"

    # ── qwen chat ─────────────────────────────────────────────────────────

    def _qwen_chat(self, user_query: str, history: list) -> str:
        messages = [
            {"role": "system", "content":
                "You are a friendly, helpful AI assistant named Jarvis. "
                "Be concise, warm, and natural. Keep responses short — "
                "they will be read aloud via TTS."}
        ]
        for msg in history[-6:]:
            content = msg.get("content", "")
            if content and not content.startswith("(screen"):
                # [FIXED - MEDIUM] cap individual history messages to avoid
                # a single long prior turn overflowing num_ctx=4096
                messages.append({"role": msg["role"], "content": content[:600]})
        messages.append({"role": "user", "content": user_query})

        payload = {
            "model": self._reasoning_model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": 0.7,
                "num_predict": 200,
                "num_ctx": 4096,
                "num_gpu": 0,
                "num_thread": 8,
            },
        }
        try:
            resp = self._session.post(
                f"{self.base_url}/api/chat",
                json=payload, timeout=(5, 60)
            )
            resp.raise_for_status()
            return resp.json()["message"]["content"].strip()
        except Exception as e:
            log.error("qwen chat error: %s", e)
            return "Sorry, I had trouble responding."

    # ── helpers ───────────────────────────────────────────────────────────

    def _model_available(self, model_name: str) -> bool:
        import time as _time
        now = _time.monotonic()
        if self._available_models is None or now > self._cache_expiry_ts:
            try:
                r = self._session.get(f"{self.base_url}/api/tags", timeout=2)
                self._available_models = {m["name"] for m in r.json().get("models", [])}
                self._cache_expiry_ts = now + 60.0
            # [FIXED - HIGH] was bare except; catches KeyboardInterrupt/SystemExit
            # and permanently caches an empty set on any error. Use except Exception.
            except Exception as e:
                log.warning("Could not fetch model list from Ollama: %s", e)
                self._available_models = set()
                self._cache_expiry_ts = now + 5.0   # retry sooner on failure
        return any(model_name in m for m in self._available_models)

    def _collect_generate_stream(self, resp) -> str:
        parts = []
        for line in resp.iter_lines():
            if not line:
                continue
            try:
                chunk = json.loads(line)
                parts.append(chunk.get("response", ""))
                if chunk.get("done"):
                    break
            except json.JSONDecodeError:
                continue
        return "".join(parts)

    def is_available(self) -> bool:
        try:
            r = self._session.get(f"{self.base_url}/api/tags", timeout=2)
            return r.status_code == 200
        except Exception:
            return False
