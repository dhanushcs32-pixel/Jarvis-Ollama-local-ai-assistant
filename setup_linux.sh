#!/bin/bash
# ============================================================
# Vision Assistant — Linux Setup
# ============================================================
# HOW TO RUN:
#   chmod +x setup_linux.sh   (first time only)
#   ./setup_linux.sh
# ============================================================

set -e

echo ""
echo "============================================"
echo "  Vision Assistant — Linux Setup"
echo "============================================"
echo ""

# ── 1. Python 3.11 ──────────────────────────────────────────
echo "[1/3] Checking Python 3.11..."
if command -v python3.11 &>/dev/null; then
    echo "      OK — $(python3.11 --version)"
else
    echo ""
    echo "  ERROR: Python 3.11 not found. Install it with:"
    echo ""
    echo "      sudo apt install python3.11 python3.11-venv python3.11-dev"
    echo ""
    echo "  Then re-run this script."
    echo ""
    exit 1
fi

# ── 2. Pip + system dependencies ────────────────────────────
echo ""
echo "[2/3] Installing Python packages..."
python3.11 -m pip install -r requirements.txt

echo ""
echo "      Installing system packages (requires sudo)..."
sudo apt-get update -qq

PKGS=()
command -v espeak       &>/dev/null || PKGS+=(espeak)
command -v brightnessctl &>/dev/null || PKGS+=(brightnessctl)
command -v wmctrl       &>/dev/null || PKGS+=(wmctrl)

if [ ${#PKGS[@]} -gt 0 ]; then
    echo "      Installing: ${PKGS[*]}"
    sudo apt-get install -y "${PKGS[@]}"
else
    echo "      All system packages already installed."
fi

echo ""
echo "      Checking audio backend..."
if command -v pactl &>/dev/null; then
    echo "      PulseAudio (pactl) — OK"
elif command -v wpctl &>/dev/null; then
    echo "      PipeWire (wpctl) — OK"
else
    echo ""
    echo "  WARNING: Neither pactl nor wpctl found."
    echo "  Volume control will not work."
    echo "  Install PulseAudio:  sudo apt install pulseaudio"
    echo ""
fi

echo "      Done."

# ── 3. Tesseract ────────────────────────────────────────────
echo ""
echo "[3/3] Checking Tesseract OCR..."
if command -v tesseract &>/dev/null; then
    echo "      OK — $(tesseract --version 2>&1 | head -1)"
else
    echo ""
    echo "  Tesseract not found. Install it with:"
    echo ""
    echo "      sudo apt install tesseract-ocr"
    echo ""
    echo "  Then re-run this script."
    echo ""
    exit 1
fi

# ── Done ────────────────────────────────────────────────────
echo ""
echo "============================================"
echo "  Setup complete."
echo "  Run the assistant with:"
echo "      python3.11 launch.py"
echo "============================================"
echo ""
