@echo off
setlocal enabledelayedexpansion
title Vision Assistant — Setup

echo.
echo ============================================================
echo   VISION ASSISTANT — SETUP
echo ============================================================
echo.

:: ── Step 1: Check Python 3.11 ───────────────────────────────────────────────
echo [1/5] Checking Python 3.11...
py -3.11 --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo   ERROR: Python 3.11 not found.
    echo   Download it from: https://www.python.org/downloads/release/python-3119/
    echo   Make sure to check "Add Python to PATH" during install.
    echo.
    goto :fail
)
for /f "tokens=*" %%v in ('py -3.11 --version 2^>^&1') do echo   Found: %%v
echo.

:: ── Step 2: Install Python packages ─────────────────────────────────────────
echo [2/5] Installing Python packages from requirements.txt...
echo   This may take several minutes on first run — torch and faster-whisper
echo   are large downloads. Please wait, it is not frozen.
echo.
py -3.11 -m pip install --upgrade pip --quiet
py -3.11 -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo   ERROR: One or more packages failed to install.
    echo   Check the output above for details.
    echo.
    goto :fail
)
echo.
echo   All packages installed successfully.
echo.

:: ── Step 2b: pywin32 post-install (required or win32api imports will fail) ──
echo   Running pywin32 post-install step...
py -3.11 -c "import pywin32_postinstall" >nul 2>&1
py -3.11 Scripts\pywin32_postinstall.py -install >nul 2>&1
if errorlevel 1 (
    :: Try alternate path (pip installs Scripts differently on some setups)
    for /f "tokens=*" %%p in ('py -3.11 -c "import sys; print(sys.prefix)"') do set "PYPREFIX=%%p"
    py -3.11 "!PYPREFIX!\Scripts\pywin32_postinstall.py" -install >nul 2>&1
)
echo   pywin32 post-install done.
echo.

:: ── Step 3: Check config.json exists ────────────────────────────────────────
echo [3/5] Checking config.json...
if not exist config.json (
    echo.
    echo   WARNING: config.json not found.
    echo   Copying config.example.json to config.json...
    copy config.example.json config.json >nul 2>&1
    if errorlevel 1 (
        echo   ERROR: Could not create config.json. Copy it manually.
        echo   Run:  copy config.example.json config.json
        echo.
    ) else (
        echo   Created config.json from example. Edit it to set your models.
        echo   See CONFIG.txt for model options by hardware tier.
        echo.
    )
) else (
    echo   Found config.json OK.
    echo.
)

:: ── Step 4: Check Tesseract OCR ─────────────────────────────────────────────
echo [4/5] Checking Tesseract OCR...
tesseract --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo   WARNING: Tesseract OCR not found on PATH.
    echo   The assistant will still run but icon/text detection on screen will not work.
    echo.
    echo   To fix this:
    echo     1. Download:
    echo        https://github.com/tesseract-ocr/tesseract/releases/download/5.5.0/tesseract-ocr-w64-setup-5.5.0.20241111.exe
    echo     2. Run the installer — keep the default install path.
    echo     3. Add  C:\Program Files\Tesseract-OCR\  to your system PATH:
    echo        Start → search "Environment Variables" → Path → Edit → New → paste path above.
    echo     4. Also set TESSDATA_PREFIX environment variable to:
    echo        C:\Program Files\Tesseract-OCR\tessdata
    echo        (same Environment Variables window, under System Variables → New)
    echo     5. Restart this terminal and re-run setup.bat to confirm.
    echo.
) else (
    for /f "tokens=*" %%v in ('tesseract --version 2^>^&1 ^| findstr /i "tesseract"') do echo   Found: %%v

    :: Check TESSDATA_PREFIX is set
    if not defined TESSDATA_PREFIX (
        echo.
        echo   WARNING: TESSDATA_PREFIX environment variable is not set.
        echo   Tesseract may fail to find language data at runtime.
        echo   Set it to:  C:\Program Files\Tesseract-OCR\tessdata
        echo   (Start → search "Environment Variables" → System Variables → New)
    )
    echo.
)

:: ── Step 5: Check Ollama + models ───────────────────────────────────────────
echo [5/5] Checking Ollama...
ollama --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo   WARNING: Ollama not found.
    echo   The assistant cannot run without Ollama.
    echo.
    echo   Download and install from: https://ollama.com/download/windows
    echo   After installing, run Ollama once to start the background service,
    echo   then pull your chosen models. See CONFIG.txt for model options.
    echo.
) else (
    for /f "tokens=*" %%v in ('ollama --version 2^>^&1') do echo   Found: %%v
    echo.
    echo   Checking for required models (reading from config.json)...
    echo.

    :: Parse vision_model from config.json
    for /f "tokens=2 delims=:, " %%m in ('findstr /i "vision_model" config.json') do (
        set "VISION_MODEL=%%~m"
    )
    :: Parse reasoning_model from config.json
    for /f "tokens=2 delims=:, " %%m in ('findstr /i "reasoning_model" config.json') do (
        set "REASONING_MODEL=%%~m"
    )

    if defined VISION_MODEL (
        echo   Vision model set to:    !VISION_MODEL!
        ollama show !VISION_MODEL! >nul 2>&1
        if errorlevel 1 (
            echo   NOT INSTALLED — run:  ollama pull !VISION_MODEL!
        ) else (
            echo   Status: installed OK
        )
    ) else (
        echo   WARNING: Could not read vision_model from config.json.
        echo   Make sure config.json exists and contains a "vision_model" key.
    )
    echo.
    if defined REASONING_MODEL (
        echo   Reasoning model set to: !REASONING_MODEL!
        ollama show !REASONING_MODEL! >nul 2>&1
        if errorlevel 1 (
            echo   NOT INSTALLED — run:  ollama pull !REASONING_MODEL!
        ) else (
            echo   Status: installed OK
        )
    ) else (
        echo   WARNING: Could not read reasoning_model from config.json.
        echo   Make sure config.json exists and contains a "reasoning_model" key.
    )
    echo.
)

:: ── Done ────────────────────────────────────────────────────────────────────
echo ============================================================
echo   Setup complete.
echo.
echo   If everything above shows OK or WARNING (not ERROR):
echo     py -3.11 launch.py
echo.
echo   Startup widget (optional):
echo     py -3.11 jarvis_launcher.py
echo.
echo   Add to Windows Startup (run once):
echo     py -3.11 add_to_startup.py
echo.
echo   To change your models, edit config.json.
echo   See CONFIG.txt for a full model list by hardware tier.
echo ============================================================
echo.
pause
exit /b 0

:fail
echo ============================================================
echo   Setup did not complete. Fix the error above and re-run.
echo ============================================================
echo.
pause
exit /b 1
