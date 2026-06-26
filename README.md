# Jarvis — Local AI Assistant

A local, private, voice and vision AI assistant.
No cloud. Everything runs on your machine via Ollama.

## Requirements

- Python 3.11
- [Ollama](https://ollama.ai) installed and running

## Setup

See **SETUP.txt** for full instructions — model recommendations by hardware, manual install steps, and troubleshooting.

**Quick start:**

```bash
python3.11 install.py
python3.11 launch.py
```

> Windows: use `py -3.11` instead of `python3.11`

## Widget Launcher (optional)

```bash
python3.11 jarvis_launcher.py
```

Click the arc reactor HUD to start Jarvis.

## Auto-start on boot (Windows only)

```bash
py -3.11 add_to_startup.py
```

## Hotkeys

| Key | Action |
|-----|--------|
| F8 | Push to talk |
| F9 | Screen analyze |
| F10 | Text input |

Wake word: **Jarvis** / **Jarv**
