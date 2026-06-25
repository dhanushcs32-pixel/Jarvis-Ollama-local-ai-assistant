"""
Memory  ─  rolling conversation history with JSON persistence.

Keeps the last N turns in RAM and optionally writes to disk so sessions
can resume. Large histories spike RAM on iGPU systems; max_history_turns=4
keeps context manageable while providing short-term continuity.
"""

import json
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


class Memory:
    def __init__(self, cfg):
        self.cfg = cfg
        self.max_turns = cfg.max_history_turns
        self._history: list[dict] = []
        self._path = Path(cfg.memory_file)
        self._load()

    def add_turn(self, user: str, assistant: str):
        self._history.append({"role": "user",      "content": user})
        self._history.append({"role": "assistant", "content": assistant})
        # Trim to window (each turn = 2 messages)
        max_msgs = self.max_turns * 2
        if len(self._history) > max_msgs:
            self._history = self._history[-max_msgs:]
        self._save()

    def get_recent(self) -> list[dict]:
        return list(self._history)

    def clear(self):
        self._history = []
        self._save()
        log.info("Memory cleared.")

    # ── Persistence ────────────────────────────────────────────────────────

    def _load(self):
        if self._path.exists():
            try:
                with open(self._path) as f:
                    data = json.load(f)
                # [FIXED - MEDIUM] validate loaded JSON is a list before use;
                # a corrupted file returning a dict/null would crash on append()
                if not isinstance(data, list):
                    log.warning(
                        "memory.json contained %s instead of a list — resetting.",
                        type(data).__name__,
                    )
                    self._history = []
                    self._save()
                else:
                    self._history = data
                # [FIXED - LOW] was f-string; replaced with % formatting
                log.debug("Loaded %d memory messages.", len(self._history))
            except Exception as e:
                log.warning("Could not load memory file: %s", e)

    def _save(self):
        try:
            with open(self._path, "w") as f:
                json.dump(self._history, f, indent=2)
        except Exception as e:
            log.warning("Could not save memory: %s", e)
