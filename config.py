"""
VisionCAD configuration — resolves paths for FreeCAD, Claude CLI, and project directories.

Priority order for each path:
  1. Environment variable (VISIONCAD_FREECAD, VISIONCAD_CLAUDE, etc.)
  2. User config file (~/.visioncad/config.json)
  3. Platform-aware defaults / shutil.which() auto-detection
"""
import json
import os
import platform
import shutil
import sys
import tempfile

# Project root = directory containing this file
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

# Platform detection
PLATFORM = platform.system()  # "Darwin", "Linux", "Windows"

# Temp directory (platform-safe)
TEMP_DIR = tempfile.gettempdir()


def _user_config_path():
    return os.path.expanduser("~/.visioncad/config.json")


def _load_user_config():
    path = _user_config_path()
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}


def save_user_config(config):
    path = _user_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(config, f, indent=2)


def _find_freecad():
    """Find the FreeCAD command-line binary."""
    # 1. Environment variable
    env = os.environ.get("VISIONCAD_FREECAD")
    if env and os.path.isfile(env):
        return env

    # 2. User config
    cfg = _load_user_config()
    if cfg.get("freecad_path") and os.path.isfile(cfg["freecad_path"]):
        return cfg["freecad_path"]

    # 3. Platform defaults
    candidates = []
    if PLATFORM == "Darwin":
        candidates = [
            "/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd",
            "/Applications/FreeCAD.app/Contents/MacOS/FreeCADCmd",
        ]
    elif PLATFORM == "Linux":
        candidates = [
            "/usr/bin/freecadcmd",
            "/usr/local/bin/freecadcmd",
            "/snap/freecad/current/usr/bin/freecadcmd",
        ]
    elif PLATFORM == "Windows":
        candidates = [
            r"C:\Program Files\FreeCAD\bin\FreeCADCmd.exe",
            r"C:\Program Files (x86)\FreeCAD\bin\FreeCADCmd.exe",
        ]

    for c in candidates:
        if os.path.isfile(c):
            return c

    # 4. PATH lookup
    found = shutil.which("freecadcmd") or shutil.which("FreeCADCmd")
    if found:
        return found

    return None


def _find_claude():
    """Find the Claude CLI binary."""
    env = os.environ.get("VISIONCAD_CLAUDE")
    if env and os.path.isfile(env):
        return env

    cfg = _load_user_config()
    if cfg.get("claude_path") and os.path.isfile(cfg["claude_path"]):
        return cfg["claude_path"]

    found = shutil.which("claude")
    if found:
        return found

    # Common install locations
    candidates = [
        os.path.expanduser("~/.local/bin/claude"),
        os.path.expanduser("~/.claude/bin/claude"),
        "/usr/local/bin/claude",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c

    return None


def get_projects_dir():
    """Return the projects directory, creating it if needed."""
    env = os.environ.get("VISIONCAD_PROJECTS")
    if env:
        os.makedirs(env, exist_ok=True)
        return env

    cfg = _load_user_config()
    if cfg.get("projects_dir") and os.path.isdir(cfg["projects_dir"]):
        return cfg["projects_dir"]

    # Default
    default = os.path.join(os.path.expanduser("~/Documents"), "VisionCAD")
    os.makedirs(default, exist_ok=True)
    return default


def get_freecad_mod_dir():
    """Return the FreeCAD Mod directory for installing plugins."""
    if PLATFORM == "Darwin":
        return os.path.expanduser("~/Library/Application Support/FreeCAD/Mod")
    elif PLATFORM == "Linux":
        # Try XDG first, then legacy
        xdg = os.path.expanduser("~/.config/FreeCAD/Mod")
        legacy = os.path.expanduser("~/.FreeCAD/Mod")
        return xdg if os.path.isdir(os.path.dirname(xdg)) else legacy
    elif PLATFORM == "Windows":
        appdata = os.environ.get("APPDATA", "")
        return os.path.join(appdata, "FreeCAD", "Mod")
    return os.path.expanduser("~/.FreeCAD/Mod")


# Resolved paths (cached at import time)
FREECAD_CMD = _find_freecad()
CLAUDE_CMD = _find_claude()
PROJECTS_DIR = get_projects_dir()

# System prompt files
ANALYZE_PROMPT_FILE = os.path.join(PROJECT_ROOT, "system_prompt_analyze.txt")
CODEGEN_PROMPT_FILE = os.path.join(PROJECT_ROOT, "system_prompt_codegen.txt")
SYSTEM_PROMPT_FILE = os.path.join(PROJECT_ROOT, "system_prompt.txt")
