# Vision Assistant — UI

## Install dependencies

```bash
pip install PyQt6 psutil
pip install GPUtil          # optional — enables GPU% in stats bar
```

> **Linux note:** if the stats bar GPU% shows `–%`, install `GPUtil` and ensure
> your GPU driver exposes utilisation via NVML (NVIDIA) or equivalent.

## Directory layout

```
vision_assistant/
  app.py            ← entry point (replaces main.py for UI mode)
  ui/
    __init__.py
    eye_widget.py
    log_panel.py
    overlay.py
    stats_bar.py
    text_input.py
  assets/
    eye.png         ← blue ring image (widget falls back to drawn rings if absent)
```

## Run

```bash
# Windows
py -3.11 app.py

# macOS / Linux
python3 app.py
```

## Widget reference

| Widget | Behaviour |
|--------|-----------|
| **Eye** | Floats beside cursor at all times. Pulses when listening, spins when thinking, glows when speaking. |
| **Log panel** | Frosted glass panel, bottom-right. Shows your voice / text commands and AI responses. Scrollable. |
| **Drawing overlay** | Appears when the AI returns an `annotate` action. Draws arrows, circles, boxes, and labels on screen. Press **Esc** or **F9** to dismiss early. Auto-dismisses after **10 s**. Hides the log panel while active. |
| **Stats bar** | Hidden by default. Say **"show stats"** to show, **"hide stats"** to hide. Displays CPU%, RAM%, GPU% (if GPUtil is installed), and the active model name. |
| **Text input bar** | Press **F10** to open a floating text field. Type any command and press **Enter** — routed through the same pipeline as voice commands. **Esc** or **F10** closes it. |

## Hotkeys

| Key | Action |
|-----|--------|
| F8 (hold) | Push-to-talk voice command |
| F9 | Analyse screen without voice |
| F10 | Toggle floating text input bar |
| Esc | Dismiss drawing overlay / close text input bar |

## Agent annotation format

For the overlay to activate, the agent response must include an `annotate` action.
Example agent output:

```json
{
  "description": "Here's how to drag the file.",
  "actions": [{
    "action": "annotate",
    "annotations": [
      {"type": "circle", "x": 300, "y": 400, "r": 35, "label": "Pick up here"},
      {"type": "arrow",  "x1": 300, "y1": 400, "x2": 700, "y2": 300, "label": "Drag to here"},
      {"type": "circle", "x": 700, "y": 300, "r": 35, "label": "Drop here"}
    ],
    "instructions": [
      "Click and hold the file in the left panel",
      "Drag it to the highlighted folder on the right",
      "Release to drop"
    ]
  }]
}
```

Supported annotation types: `circle`, `arrow`, `box`, `text`.

## Models

| Role | Model |
|------|-------|
| Default (chat, vision, screen analysis) | `moondream:1.8b` |
| Math / code / deep reasoning | `qwen2.5:3b` |
