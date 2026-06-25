#!/bin/bash
# ============================================================
# Vision Assistant — Mac Setup
# ============================================================
# HOW TO RUN:
#   chmod +x setup_mac.sh   (first time only)
#   ./setup_mac.sh
# ============================================================

set -e

echo ""
echo "============================================"
echo "  Vision Assistant — Mac Setup"
echo "============================================"
echo ""

# ── 1. Python 3.11 ──────────────────────────────────────────
echo "[1/3] Checking Python 3.11..."
if command -v python3.11 &>/dev/null; then
    echo "      OK — $(python3.11 --version)"
else
    echo ""
    echo "  ERROR: Python 3.11 not found."
    echo "  Download: https://www.python.org/downloads/macos/"
    echo "  Install it, then re-run this script."
    echo ""
    exit 1
fi

# ── 2. Pip + system dependencies ────────────────────────────
echo ""
echo "[2/3] Installing Python packages..."
python3.11 -m pip install -r requirements.txt
python3.11 -m pip install pyobjc-framework-Cocoa

echo ""
echo "      Checking Homebrew system packages..."

if ! command -v brew &>/dev/null; then
    echo ""
    echo "  ERROR: Homebrew not found."
    echo "  Install it from: https://brew.sh"
    echo "  Then re-run this script."
    echo ""
    exit 1
fi

if ! command -v brightness &>/dev/null; then
    echo "      Installing brightness CLI..."
    brew install brightness
else
    echo "      brightness CLI — OK"
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
    echo "      brew install tesseract"
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
