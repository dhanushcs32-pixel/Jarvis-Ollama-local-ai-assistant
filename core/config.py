"""
Configuration  ─  edit values here or override via env vars / config.json.
Designed for Intel iGPU + 16 GB shared RAM: low resolution captures,
small context window, single-turn memory to minimise VRAM pressure.
"""

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path

log = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent.parent / "config.json"


@dataclass
class Config:
    # ── Model ──────────────────────────────────────────────────────────────
    model: str = "moondream:1.8b"         # legacy key — kept for back-compat
    vision_model: str = "moondream:1.8b"  # handles chat, screen analysis, icon finding
    reasoning_model: str = "qwen2.5:3b"  # handles math, code, deep reasoning
    ollama_host: str = "http://localhost:11434"
    max_tokens: int = 512          # keep low to reduce decode latency
    temperature: float = 0.1       # deterministic for agentic tasks

    # ── Screen capture ─────────────────────────────────────────────────────
    capture_width: int = 1280      # downsample; full 1920 is wasteful
    capture_height: int = 720
    capture_quality: int = 60      # JPEG quality for base64 payload size

    # ── Memory / context ───────────────────────────────────────────────────
    max_history_turns: int = 4     # rolling window to cap context tokens
    memory_file: str = "memory.json"

    # ── Hotkeys ────────────────────────────────────────────────────────────
    hotkey_screenshot: str = "f9"
    hotkey_voice: str = "f8"       # hold to record

    # ── Voice ──────────────────────────────────────────────────────────────
    voice_enabled: bool = True
    whisper_model: str = "base"    # tiny / base / small  (tiny = ~39 MB)
    whisper_device: str = "cpu"    # iGPU has no CUDA; use CPU
    whisper_compute: str = "int8"  # quantized for speed on CPU
    tts_rate: int = 175            # pyttsx3 words-per-minute
    tts_volume: float = 0.9
    audio_sample_rate: int = 16000
    audio_channels: int = 1
    voice_hold_key: str = "f8"    # same as hotkey_voice; hold to record

    # ── Action safety ──────────────────────────────────────────────────────
    require_confirmation: bool = False   # set True to prompt before actions
    allowed_actions: list = field(default_factory=lambda: [
        "click", "type", "scroll", "open_app", "search_file",
        "press_key", "move_mouse", "speak", "describe",
        "volume_up", "volume_down", "volume_mute",
        "brightness_up", "brightness_down",
        "todo_add", "todo_read", "todo_clear", "todo_done",
        "open_url", "whatsapp_send",
    ])

    @classmethod
    def load(cls) -> "Config":
        """Load from config.json if present, otherwise use defaults."""
        cfg = cls()
        if CONFIG_PATH.exists():
            # [FIXED - MEDIUM] guard against malformed config.json crashing startup
            try:
                with open(CONFIG_PATH) as f:
                    overrides = json.load(f)
                for k, v in overrides.items():
                    if hasattr(cfg, k):
                        setattr(cfg, k, v)
            except json.JSONDecodeError as e:
                log.warning("config.json is malformed (%s) — using defaults.", e)

        # Also accept env-var overrides: ASSISTANT_MODEL, ASSISTANT_HOTKEY, …
        # [FIXED - MEDIUM] cast env strings to the correct type; log invalid values
        for field_name in asdict(cfg):
            env_key = f"ASSISTANT_{field_name.upper()}"
            if env_key in os.environ:
                raw = os.environ[env_key]
                existing = getattr(cfg, field_name)
                try:
                    if isinstance(existing, bool):
                        setattr(cfg, field_name, raw.lower() in ("1", "true", "yes"))
                    elif isinstance(existing, int):
                        setattr(cfg, field_name, int(raw))
                    elif isinstance(existing, float):
                        setattr(cfg, field_name, float(raw))
                    else:
                        setattr(cfg, field_name, raw)
                except (ValueError, TypeError):
                    log.warning(
                        "Invalid env override %s=%r — keeping default %r.",
                        env_key, raw, existing,
                    )
        return cfg

    def save(self):
        # [FIXED - LOW] guard against permissions error / full disk
        try:
            with open(CONFIG_PATH, "w") as f:
                json.dump(asdict(self), f, indent=2)
        except Exception as e:
            log.error("Failed to save config.json: %s", e)
