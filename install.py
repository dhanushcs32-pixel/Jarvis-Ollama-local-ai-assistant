#!/usr/bin/env python3
"""
install.py — Vision Assistant Interactive Installer
====================================================
Supports Windows, macOS, and Linux.
Run with:
    python install.py          (or python3 install.py on mac/linux)

What it does:
  1. Detects your OS
  2. Checks Python version
  3. Checks Ollama
  4. Lets you pick your vision + text models from a menu
  5. Installs all Python packages (skips Windows-only ones on mac/linux)
  6. Installs required system packages (brew / apt)
  7. Checks Tesseract OCR
  8. Writes config.json with your chosen models
  9. Pulls your chosen Ollama models
  10. Prints a final summary
"""

import sys
import os
import platform
import subprocess
import shutil
import json
import textwrap
from pathlib import Path

# ── Colour helpers (ANSI — disabled on Windows cmd that doesn't support it) ──

_USE_COLOUR = sys.stdout.isatty() and (
    sys.platform != "win32" or os.environ.get("WT_SESSION")  # Windows Terminal supports ANSI
)

def _c(code: str, text: str) -> str:
    if not _USE_COLOUR:
        return text
    codes = {
        "cyan":   "\033[96m",
        "green":  "\033[92m",
        "yellow": "\033[93m",
        "red":    "\033[91m",
        "bold":   "\033[1m",
        "dim":    "\033[2m",
        "reset":  "\033[0m",
    }
    return f"{codes.get(code, '')}{text}{codes['reset']}"

def ok(msg):    print(f"  {_c('green',  '✓')} {msg}")
def warn(msg):  print(f"  {_c('yellow', '!')} {msg}")
def err(msg):   print(f"  {_c('red',    '✗')} {msg}")
def info(msg):  print(f"  {_c('dim',    '·')} {msg}")
def step(n, total, msg): print(f"\n{_c('cyan', _c('bold', f'[{n}/{total}]'))} {_c('bold', msg)}")
def header(msg): print(f"\n{_c('cyan', '═' * 60)}\n  {_c('bold', msg)}\n{_c('cyan', '═' * 60)}")
def rule():      print(_c('dim', '  ' + '─' * 56))


# ── Platform detection ─────────────────────────────────────────────────────

IS_WIN   = sys.platform == "win32"
IS_MAC   = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")
OS_NAME  = "Windows" if IS_WIN else ("macOS" if IS_MAC else "Linux")


# ── Model catalogue ───────────────────────────────────────────────────────

VISION_MODELS = [
    # (display_label, model_tag, tier, vram, size, notes)
    ("moondream:1.8b",  "moondream:1.8b",  "Minimum",   "~2 GB RAM",   "~1.6 GB", "CPU-friendly default ★"),
    ("moondream2",      "moondream2",       "Minimum",   "~2 GB RAM",   "~1.9 GB", "Slightly larger CPU model"),
    ("llava:7b",        "llava:7b",         "Entry GPU", "~6 GB VRAM",  "~4.7 GB", "Good for RTX 3060/4060"),
    ("bakllava",        "bakllava",         "Entry GPU", "~6 GB VRAM",  "~4.1 GB", "Best for reading screen text"),
    ("llava-phi3",      "llava-phi3",       "Mid GPU",   "~5 GB VRAM",  "~3.8 GB", "Efficient mid-range option"),
    ("llava:13b",       "llava:13b",        "Mid GPU",   "~10 GB VRAM", "~8.0 GB", "High quality, RTX 3080"),
    ("qwen2.5vl:7b",    "qwen2.5vl:7b",    "Mid GPU",   "~6 GB VRAM",  "~4.8 GB", "Excellent OCR + reasoning"),
    ("llava:34b",       "llava:34b",        "High GPU",  "~22 GB VRAM", "~20 GB",  "RTX 3090/4090"),
    ("qwen2.5vl:72b",   "qwen2.5vl:72b",   "High GPU",  "~48 GB VRAM", "~41 GB",  "Maximum quality"),
]

TEXT_MODELS = [
    ("qwen2.5:3b",      "qwen2.5:3b",      "Minimum",   "~3 GB RAM",   "~2.0 GB", "CPU-friendly default ★"),
    ("llama3.2:3b",     "llama3.2:3b",      "Minimum",   "~3 GB RAM",   "~2.0 GB", "Good general reasoning"),
    ("qwen2.5:7b",      "qwen2.5:7b",       "Entry GPU", "~5 GB VRAM",  "~4.7 GB", "RTX 3060/4060"),
    ("mistral:7b",      "mistral:7b",       "Entry GPU", "~5 GB VRAM",  "~4.1 GB", "Strong instruction model"),
    ("llama3.1:8b",     "llama3.1:8b",      "Entry GPU", "~6 GB VRAM",  "~4.9 GB", "Meta's flagship 8B"),
    ("qwen2.5:14b",     "qwen2.5:14b",      "Mid GPU",   "~10 GB VRAM", "~8.9 GB", "Near GPT-4o-mini quality"),
    ("deepseek-r1:8b",  "deepseek-r1:8b",   "Mid GPU",   "~6 GB VRAM",  "~4.9 GB", "Strong reasoning/math"),
    ("qwen2.5:32b",     "qwen2.5:32b",      "High GPU",  "~22 GB VRAM", "~19 GB",  "RTX 3090/4090"),
    ("deepseek-r1:32b", "deepseek-r1:32b",  "High GPU",  "~22 GB VRAM", "~19 GB",  "Best math/science"),
]

TIER_PRESETS = {
    "1": {
        "label": "No GPU / CPU only",
        "vision": "moondream:1.8b",
        "text":   "qwen2.5:3b",
    },
    "2": {
        "label": "Entry GPU  (4–6 GB VRAM — RTX 3060, RX 6600)",
        "vision": "llava:7b",
        "text":   "qwen2.5:7b",
    },
    "3": {
        "label": "Mid GPU    (8–12 GB VRAM — RTX 3070/3080)",
        "vision": "qwen2.5vl:7b",
        "text":   "qwen2.5:14b",
    },
    "4": {
        "label": "High GPU   (20–24 GB VRAM — RTX 3090, RTX 4090)",
        "vision": "llava:34b",
        "text":   "deepseek-r1:32b",
    },
    "5": {
        "label": "Single model (vision + reasoning — mid GPU)",
        "vision": "qwen2.5vl:7b",
        "text":   "qwen2.5vl:7b",
    },
}


# ── Menu helpers ──────────────────────────────────────────────────────────

def _pick_from_list(prompt: str, models: list) -> str:
    """
    Show a numbered menu of models grouped by tier.
    Returns the chosen model tag.
    """
    current_tier = None
    print()
    for i, (label, tag, tier, vram, size, notes) in enumerate(models, 1):
        if tier != current_tier:
            current_tier = tier
            print(f"  {_c('yellow', _c('bold', f'── {tier} ──'))}")
        num  = _c('cyan', f"  [{i:2d}]")
        name = _c('bold', f"{label:<20}")
        meta = _c('dim', f"{vram:<14} {size:<10}")
        print(f"{num}  {name}  {meta}  {notes}")
    print()

    while True:
        try:
            raw = input(f"  {prompt} [1-{len(models)}]: ").strip()
            idx = int(raw) - 1
            if 0 <= idx < len(models):
                chosen = models[idx]
                ok(f"Selected: {_c('bold', chosen[0])}  ({chosen[5]})")
                return chosen[1]   # model tag
        except (ValueError, KeyboardInterrupt):
            pass
        warn(f"Please enter a number between 1 and {len(models)}.")


def _pick_models() -> tuple[str, str]:
    """Interactive model selection. Returns (vision_tag, text_tag)."""
    print(f"\n{_c('bold', '  How would you like to choose your models?')}\n")
    print(f"  {_c('cyan', '[1]')}  Quick preset  (recommended — pick by hardware tier)")
    print(f"  {_c('cyan', '[2]')}  Manual select  (choose each model individually)")
    print()

    while True:
        raw = input("  Choice [1/2]: ").strip()
        if raw in ("1", "2"):
            break
        warn("Please enter 1 or 2.")

    if raw == "1":
        # ── Preset picker ──
        print(f"\n{_c('bold', '  Select your hardware tier:')}\n")
        for key, preset in TIER_PRESETS.items():
            print(f"  {_c('cyan', f'[{key}]')}  {preset['label']}")
            info(f"       Vision: {preset['vision']}   Text: {preset['text']}")
        print()
        while True:
            k = input(f"  Tier [1-{len(TIER_PRESETS)}]: ").strip()
            if k in TIER_PRESETS:
                p = TIER_PRESETS[k]
                ok(f"Vision model : {_c('bold', p['vision'])}")
                ok(f"Text model   : {_c('bold', p['text'])}")
                return p["vision"], p["text"]
            warn(f"Please enter a number between 1 and {len(TIER_PRESETS)}.")

    else:
        # ── Manual picker ──
        print(f"\n{_c('bold', '  ── VISION MODEL ──')}")
        info("Used for screen analysis, icon finding, visual understanding.")
        vision = _pick_from_list("Choose vision model", VISION_MODELS)

        print(f"\n{_c('bold', '  ── TEXT / REASONING MODEL ──')}")
        info("Used for chat, math, code, and general reasoning.")
        text = _pick_from_list("Choose text model", TEXT_MODELS)

        return vision, text


# ── Step implementations ──────────────────────────────────────────────────

def check_python() -> bool:
    v = sys.version_info
    ver_str = f"{v.major}.{v.minor}.{v.micro}"
    if v < (3, 10):
        err(f"Python {ver_str} found — Python 3.10+ required (3.11 recommended).")
        if IS_WIN:
            info("Download: https://www.python.org/downloads/release/python-3119/")
        elif IS_MAC:
            info("Download: https://www.python.org/downloads/macos/")
        else:
            info("Install:  sudo apt install python3.11 python3.11-dev")
        return False
    if v < (3, 11):
        warn(f"Python {ver_str} — works, but 3.11 is recommended for best compatibility.")
    else:
        ok(f"Python {ver_str}")
    return True


def check_ollama() -> bool:
    if shutil.which("ollama") is None:
        err("Ollama not found on PATH.")
        if IS_WIN:
            info("Download: https://ollama.com/download/windows")
        elif IS_MAC:
            info("Download: https://ollama.com/download/mac")
        else:
            info("Install:  curl -fsSL https://ollama.com/install.sh | sh")
        info("Install Ollama, then re-run this installer.")
        return False
    try:
        result = subprocess.run(["ollama", "--version"],
                                capture_output=True, text=True, timeout=5)
        ver = result.stdout.strip() or result.stderr.strip()
        ok(f"Ollama — {ver}")
    except Exception:
        ok("Ollama found (version check failed — that's OK)")
    return True


def install_python_packages() -> bool:
    req_file = Path(__file__).parent / "requirements.txt"
    if not req_file.exists():
        err("requirements.txt not found. Make sure install.py is in the project root.")
        return False

    # Read and filter requirements
    lines = req_file.read_text(encoding="utf-8").splitlines()
    packages = []
    for line in lines:
        stripped = line.strip()
        # Skip comments, empty lines, and the Windows-only block
        if not stripped or stripped.startswith("#"):
            continue
        # Strip inline comments (e.g. "PyQt6  # floating window") — pip rejects them
        if " #" in stripped:
            stripped = stripped[:stripped.index(" #")].strip()
        if not stripped:
            continue
        # Skip Windows-only packages on mac/linux
        win_only = {"pywinauto", "pywin32", "pygetwindow"}
        pkg_name = stripped.split("=")[0].split(">")[0].split("<")[0].split("[")[0].strip().lower()
        if not IS_WIN and pkg_name in win_only:
            info(f"Skipping Windows-only package: {stripped}")
            continue
        packages.append(stripped)

    info(f"Installing {len(packages)} packages...")
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "pip", "-q"],
            check=True,
        )
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install"] + packages,
            check=True,
        )
        ok("All Python packages installed.")

        # Windows: run pywin32 post-install
        if IS_WIN:
            info("Running pywin32 post-install step...")
            try:
                scripts_dir = Path(sys.prefix) / "Scripts"
                post = scripts_dir / "pywin32_postinstall.py"
                if post.exists():
                    subprocess.run([sys.executable, str(post), "-install"],
                                   capture_output=True)
                    ok("pywin32 post-install done.")
            except Exception as e:
                warn(f"pywin32 post-install failed (may need to run manually): {e}")

        return True
    except subprocess.CalledProcessError as e:
        err(f"Package install failed: {e}")
        return False


def install_system_packages() -> bool:
    """Install OS-level dependencies (brew on mac, apt on linux, nothing on windows)."""
    if IS_WIN:
        info("No system packages to install on Windows.")
        return True

    if IS_MAC:
        if not shutil.which("brew"):
            err("Homebrew not found.")
            info("Install it from https://brew.sh, then re-run this installer.")
            return False
        needed = {}
        if not shutil.which("tesseract"):
            needed["tesseract"] = "tesseract"
        if not shutil.which("brightness"):
            needed["brightness"] = "brightness"
        if not needed:
            ok("All Homebrew packages already installed.")
            return True
        for formula, binary in needed.items():
            info(f"brew install {formula} ...")
            try:
                subprocess.run(["brew", "install", formula], check=True)
                ok(f"Installed: {formula}")
            except subprocess.CalledProcessError:
                warn(f"brew install {formula} failed — you may need to install it manually.")
        return True

    if IS_LINUX:
        needed = []
        if not shutil.which("tesseract"):
            needed.append("tesseract-ocr")
        if not shutil.which("espeak"):
            needed.append("espeak")
        if not shutil.which("brightnessctl"):
            needed.append("brightnessctl")
        if not shutil.which("wmctrl"):
            needed.append("wmctrl")
        if not shutil.which("xdotool"):
            needed.append("xdotool")
        if not needed:
            ok("All system packages already installed.")
            return True
        info(f"Installing via apt: {', '.join(needed)}")
        try:
            subprocess.run(["sudo", "apt-get", "update", "-qq"], check=True)
            subprocess.run(["sudo", "apt-get", "install", "-y"] + needed, check=True)
            ok(f"Installed: {', '.join(needed)}")
        except subprocess.CalledProcessError:
            warn("apt install failed — you may need to run it manually:")
            info(f"  sudo apt install {' '.join(needed)}")
        return True

    return True


def check_tesseract() -> bool:
    if shutil.which("tesseract"):
        try:
            result = subprocess.run(["tesseract", "--version"],
                                    capture_output=True, text=True, timeout=5)
            first_line = (result.stdout or result.stderr).splitlines()[0]
            ok(f"Tesseract OCR — {first_line}")
        except Exception:
            ok("Tesseract OCR found.")
        return True
    else:
        warn("Tesseract OCR not found — icon/text detection will not work.")
        if IS_WIN:
            info("Download: https://github.com/tesseract-ocr/tesseract/releases")
            info("Add it to PATH and set TESSDATA_PREFIX after installing.")
        elif IS_MAC:
            info("Install: brew install tesseract")
        else:
            info("Install: sudo apt install tesseract-ocr")
        return False   # non-fatal — installer continues


def write_config(vision_model: str, text_model: str) -> bool:
    config_path    = Path(__file__).parent / "config.json"
    example_path   = Path(__file__).parent / "config.example.json"

    if config_path.exists():
        # Load existing and update only model keys
        try:
            cfg = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
        cfg["vision_model"]    = vision_model
        cfg["reasoning_model"] = text_model
        cfg["model"]           = vision_model
    elif example_path.exists():
        try:
            cfg = json.loads(example_path.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
        cfg["vision_model"]    = vision_model
        cfg["reasoning_model"] = text_model
        cfg["model"]           = vision_model
    else:
        # Minimal fallback config
        cfg = {
            "model":           vision_model,
            "vision_model":    vision_model,
            "reasoning_model": text_model,
            "ollama_host":     "http://localhost:11434",
            "max_tokens":      256,
            "temperature":     0.1,
            "tts_volume":      0.9,
            "voice_enabled":   True,
        }

    try:
        config_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        ok(f"config.json written  (vision={vision_model}, text={text_model})")
        return True
    except OSError as e:
        err(f"Could not write config.json: {e}")
        return False


def pull_models(vision_model: str, text_model: str) -> None:
    models_to_pull = list(dict.fromkeys([vision_model, text_model]))  # dedupe
    for model in models_to_pull:
        print(f"\n  {_c('cyan', '↓')} Pulling {_c('bold', model)} "
              f"(this may take a while for large models)...")
        try:
            # Stream output so user sees progress
            proc = subprocess.Popen(
                ["ollama", "pull", model],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    print(f"    {_c('dim', line)}")
            proc.wait()
            if proc.returncode == 0:
                ok(f"{model} ready.")
            else:
                warn(f"ollama pull {model} exited with code {proc.returncode}.")
                info(f"You can pull it manually later:  ollama pull {model}")
        except FileNotFoundError:
            warn("ollama not found — skipping model pull.")
            info(f"Pull manually:  ollama pull {model}")
        except Exception as e:
            warn(f"Pull failed: {e}")
            info(f"Pull manually:  ollama pull {model}")


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    header("Jarvis Vision Assistant — Installer")
    print(f"  OS detected: {_c('bold', OS_NAME)} ({platform.version()[:60]})")

    TOTAL = 7
    errors   = []
    warnings = []

    # ── Step 1: Python ────────────────────────────────────────────────────
    step(1, TOTAL, "Checking Python version")
    if not check_python():
        err("Python requirement not met — cannot continue.")
        sys.exit(1)

    # ── Step 2: Ollama ───────────────────────────────────────────────────
    step(2, TOTAL, "Checking Ollama")
    ollama_ok = check_ollama()
    if not ollama_ok:
        warn("Ollama not found. Install it and re-run. Continuing anyway...")
        warnings.append("Ollama not installed — install it before running Jarvis.")

    # ── Step 3: Model selection ───────────────────────────────────────────
    step(3, TOTAL, "Choose your AI models")
    print(f"\n  {_c('dim', 'These will be pulled from Ollama and saved to config.json.')}")
    vision_model, text_model = _pick_models()

    # ── Step 4: Python packages ───────────────────────────────────────────
    step(4, TOTAL, "Installing Python packages")
    if not install_python_packages():
        errors.append("Some Python packages failed to install.")

    # ── Step 5: System packages ───────────────────────────────────────────
    step(5, TOTAL, "Installing system packages")
    install_system_packages()

    # ── Step 6: Tesseract ────────────────────────────────────────────────
    step(6, TOTAL, "Checking Tesseract OCR")
    if not check_tesseract():
        warnings.append("Tesseract not installed — screen text detection won't work.")

    # ── Step 7: Write config + pull models ───────────────────────────────
    step(7, TOTAL, "Writing config and pulling models")
    write_config(vision_model, text_model)

    if ollama_ok:
        pull_models(vision_model, text_model)
    else:
        warn("Skipping model pull — Ollama not available.")
        info(f"Pull manually after installing Ollama:")
        info(f"  ollama pull {vision_model}")
        if text_model != vision_model:
            info(f"  ollama pull {text_model}")

    # ── Summary ───────────────────────────────────────────────────────────
    header("Installation Summary")

    if not errors and not warnings:
        print(f"  {_c('green', _c('bold', '✓ Everything installed successfully!'))}\n")
    else:
        if errors:
            print(f"  {_c('red', _c('bold', 'Errors (must fix):'))}")
            for e in errors:
                err(e)
        if warnings:
            print(f"\n  {_c('yellow', _c('bold', 'Warnings (optional):'))}")
            for w in warnings:
                warn(w)

    print(f"""
  {_c('bold', 'Your setup:')}
    Vision model : {_c('cyan', vision_model)}
    Text model   : {_c('cyan', text_model)}

  {_c('bold', 'To start Jarvis:')}
    {'py -3.11 launch.py' if IS_WIN else 'python3.11 launch.py'}

  {_c('bold', 'Optional — arc reactor launcher widget:')}
    {'py -3.11 jarvis_launcher.py' if IS_WIN else 'python3.11 jarvis_launcher.py'}

  {_c('bold', 'Optional — add to system startup (Windows only):')}
    {'py -3.11 add_to_startup.py' if IS_WIN else '(use launchd on macOS or systemd on Linux)'}

  {_c('bold', 'Hotkeys once running:')}
    F8   Push-to-talk
    F9   Analyze screen
    F10  Open text input
    Say "Jarvis" to wake hands-free

  {_c('bold', 'To change models later:')}
    Edit config.json  →  vision_model / reasoning_model
    Then: ollama pull <new-model-name>
""")

    if IS_WIN:
        input("  Press Enter to exit...")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n  {_c('yellow', 'Installation cancelled.')}")
        sys.exit(0)
