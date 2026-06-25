# Vision Assistant

A local, private, vision-enabled AI assistant for Windows.
No cloud calls — everything runs on your machine.

## Quick Start

### 1. Prerequisites

- Python 3.10 or 3.11 (3.12 works but pyttsx3 may need extra steps)
- Ollama installed and running: https://ollama.ai
- Model pulled: `ollama pull qwen3-vl:2b`

### 2. Install dependencies

```cmd
cd vision_assistant
pip install -r requirements.txt
```

### 3. Run

```cmd
python main.py
```

### 4. Use it

| Key | Action |
|-----|--------|
| **F9** | Capture screen → analyze → execute |
| **Hold F8** | Record voice → transcribe → analyze + execute |

---

## Architecture

```
[F9 / F8 hold]
      │
      ▼
ScreenCapture (mss)  +  VoiceListener (faster-whisper)
      │                         │
      └──────────┬──────────────┘
                 ▼
           OllamaClient
         qwen3-vl:2b  @  localhost:11434
                 │
                 ▼
           ActionParser  (JSON → dict)
                 │
         ┌───────┼───────────┐
         ▼       ▼           ▼
     pyautogui  subprocess  pyttsx3
    (click/type) (open app)  (TTS)
                 │
                 ▼
            Memory (JSON)
```

---

## Intel iGPU Optimization Notes

Your Intel Core Ultra 5 225H has no dedicated VRAM — it uses system RAM.
These settings in `config.json` keep the assistant from saturating memory:

| Setting | Value | Why |
|---------|-------|-----|
| `capture_width/height` | 1280 × 720 | Halves base64 payload vs 1920×1080 |
| `capture_quality` | 60 | ~60 KB vs ~300 KB for PNG |
| `max_tokens` | 512 | Short replies = less decode RAM |
| `num_ctx` (in ollama_client.py) | 2048 | Small context window |
| `num_gpu` | 0 | Force CPU inference; avoids shared-mem thrash |
| `num_thread` | 4 | Leaves 2 cores for GUI |
| `whisper_model` | `tiny` | 39 MB, runs in ~1 s on CPU int8 |
| `whisper_compute` | `int8` | 4× smaller than float32 |
| `max_history_turns` | 4 | 8 messages max in context |

**If response is too slow:**
1. Reduce `capture_quality` to 40
2. Reduce `max_tokens` to 256
3. Switch to `ollama run llava:7b-q4_K_M` if you pull it — runs faster for pure description tasks

**If Whisper is slow:**
- `tiny` should run in ~1-2 s on your CPU. If slower, check you're not running a `float16` compute type — use `int8`.

---

## Adding Actions

1. Add a new method to `actions/dispatcher.py`:
   ```python
   def _do_my_action(self, a: dict):
       # a contains the parsed JSON fields
       pass
   ```
2. Add the action name to `allowed_actions` in `config.json`.
3. Add it to the system prompt in `core/prompt.py` so the model knows to use it.

---

## Safety

- Set `"require_confirmation": true` in `config.json` to confirm every action before execution.
- The `allowed_actions` list in `config.json` is a whitelist — remove entries to disable capabilities.
- `pyautogui.FAILSAFE = True` means moving the mouse to the top-left corner stops automation.

---

## File Layout

```
vision_assistant/
├── main.py               ← entry point
├── config.json           ← edit this
├── memory.json           ← auto-created, rolling history
├── requirements.txt
├── core/
│   ├── agent.py          ← orchestrator
│   ├── capture.py        ← mss screen grab
│   ├── config.py         ← Config dataclass
│   ├── memory.py         ← rolling JSON memory
│   ├── ollama_client.py  ← HTTP client
│   └── prompt.py         ← system + user prompt builders
├── actions/
│   └── dispatcher.py     ← pyautogui / subprocess / TTS
├── voice/
│   └── listener.py       ← faster-whisper recorder
└── utils/
    └── logger.py
```
