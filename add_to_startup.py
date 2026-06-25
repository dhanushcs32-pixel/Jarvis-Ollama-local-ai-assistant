"""
add_to_startup.py — Registers jarvis_launcher.py to run on Windows startup.
============================================================
Run this ONCE:
  py -3.11 add_to_startup.py

To REMOVE from startup:
  py -3.11 add_to_startup.py --remove

SAFETY FIXES (Session 33):
  - Replaced os.system("cscript ...") with subprocess.run([...]) — no shell=True,
    eliminates shell injection via crafted path names.
  - VBS uses sys.executable (the actual running Python binary) instead of the
    bare "py -3.11" string, which may not be in PATH on all machines.
  - Temporary VBS file now written to tempfile.mkstemp() and cleaned up in a
    finally block so it is always removed even if cscript fails.
  - Added check for APPDATA env var so KeyError gives a clear message.
"""

import os
import sys
import subprocess
import tempfile

# ── Validate APPDATA early ────────────────────────────────────────────────
_appdata = os.environ.get("APPDATA")
if not _appdata:
    print("[ERROR] APPDATA environment variable is not set. Cannot locate Startup folder.")
    sys.exit(1)

STARTUP_FOLDER = os.path.join(
    _appdata,
    r"Microsoft\Windows\Start Menu\Programs\Startup",
)

PROJECT_DIR   = os.path.dirname(os.path.abspath(__file__))
LAUNCHER_PY   = os.path.join(PROJECT_DIR, "jarvis_launcher.py")
VBS_PATH      = os.path.join(PROJECT_DIR, "jarvis_launcher_silent.vbs")
SHORTCUT_PATH = os.path.join(STARTUP_FOLDER, "JarvisLauncher.lnk")

# Use the exact Python binary that is currently running this script.
# This is always correct — no PATH lookup, no version flag.
PYTHON_EXE = sys.executable


def _write_vbs():
    """Write the silent VBS launcher using the current Python executable."""
    # Escape backslashes for VBScript string literals
    py_exe_vbs  = PYTHON_EXE.replace("\\", "\\\\")
    launcher_vbs = LAUNCHER_PY.replace("\\", "\\\\")
    content = (
        "' Launches Jarvis launcher silently (no console window)\n"
        "Set WShell = CreateObject(\"WScript.Shell\")\n"
        "WShell.Run Chr(34) & \"" + py_exe_vbs + "\" & Chr(34)"
        " & \" \" & Chr(34) & \"" + launcher_vbs + "\" & Chr(34)"
        ", 0, False\n"
        "Set WShell = Nothing\n"
    )
    with open(VBS_PATH, "w") as f:
        f.write(content)


def _create_shortcut():
    """
    Write a temporary VBS that calls WScript.Shell.CreateShortcut,
    run it with cscript via subprocess (no shell=True), then clean up.
    """
    # Escape backslashes for VBScript string literals
    v = VBS_PATH.replace("\\", "\\\\")
    s = SHORTCUT_PATH.replace("\\", "\\\\")
    d = PROJECT_DIR.replace("\\", "\\\\")

    content = (
        "Set WShell = CreateObject(\"WScript.Shell\")\n"
        "Set Shortcut = WShell.CreateShortcut(\"" + s + "\")\n"
        "Shortcut.TargetPath = \"wscript.exe\"\n"
        "Shortcut.Arguments = Chr(34) & \"" + v + "\" & Chr(34)\n"
        "Shortcut.WorkingDirectory = \"" + d + "\"\n"
        "Shortcut.Description = \"Jarvis Launcher\"\n"
        "Shortcut.Save\n"
        "Set Shortcut = Nothing\n"
        "Set WShell = Nothing\n"
    )

    # Write to a temp file; always remove it even if cscript fails
    fd, tmp_path = tempfile.mkstemp(suffix=".vbs", prefix="jarvis_shortcut_")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        # subprocess with list args — no shell injection possible
        result = subprocess.run(
            ["cscript", "//nologo", tmp_path],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"[!] cscript returned {result.returncode}")
            if result.stderr:
                print(f"    stderr: {result.stderr.strip()}")
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass  # already cleaned up or never existed


def add():
    _write_vbs()
    print("[+] Created silent launcher:  " + VBS_PATH)
    _create_shortcut()
    if os.path.exists(SHORTCUT_PATH):
        print("[+] Startup shortcut created: " + SHORTCUT_PATH)
        print()
        print("    Jarvis launcher will now start automatically on login.")
        print("    To remove:  py -3.11 add_to_startup.py --remove")
    else:
        print("[!] Shortcut creation may have failed. Check:")
        print("    " + SHORTCUT_PATH)


def remove():
    removed = []
    for path in [SHORTCUT_PATH, VBS_PATH]:
        if os.path.exists(path):
            try:
                os.remove(path)
                removed.append(path)
            except OSError as e:
                print(f"[!] Could not remove {path}: {e}")
    if removed:
        for p in removed:
            print("[-] Removed: " + p)
        print("\n    Jarvis launcher will no longer start on login.")
    else:
        print("[i] Nothing to remove — not registered for startup.")


if __name__ == "__main__":
    if "--remove" in sys.argv:
        remove()
    else:
        add()
