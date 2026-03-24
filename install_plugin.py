#!/usr/bin/env python3
"""Install the VisionCAD FreeCAD plugin by symlinking into FreeCAD's Mod directory."""
import os
import sys

import json

from config import (
    get_freecad_mod_dir, get_projects_dir, FREECAD_CMD,
    PROJECT_ROOT, save_user_config, _load_user_config,
)

def main():
    mod_dir = get_freecad_mod_dir()
    plugin_source = os.path.join(PROJECT_ROOT, "freecad_plugin", "VisionCAD")
    plugin_dest = os.path.join(mod_dir, "VisionCAD")

    if not os.path.isdir(plugin_source):
        print(f"Error: plugin source not found at {plugin_source}")
        sys.exit(1)

    os.makedirs(mod_dir, exist_ok=True)

    if os.path.exists(plugin_dest):
        if os.path.islink(plugin_dest):
            current = os.readlink(plugin_dest)
            if os.path.realpath(current) == os.path.realpath(plugin_source):
                print(f"Already installed: {plugin_dest} -> {plugin_source}")
            else:
                os.remove(plugin_dest)
                os.symlink(plugin_source, plugin_dest)
                print(f"Updated symlink: {plugin_dest} -> {plugin_source}")
        else:
            print(f"Error: {plugin_dest} exists and is not a symlink. Remove it manually.")
            sys.exit(1)
    else:
        os.symlink(plugin_source, plugin_dest)
        print(f"Installed: {plugin_dest} -> {plugin_source}")

    # Save repo root to config so the plugin can find it
    config = _load_user_config()
    config["repo_root"] = PROJECT_ROOT
    save_user_config(config)
    print(f"Saved repo_root to ~/.visioncad/config.json")

    # Generate .claude/settings.local.json with machine-specific paths
    local_settings_path = os.path.join(PROJECT_ROOT, ".claude", "settings.local.json")
    os.makedirs(os.path.dirname(local_settings_path), exist_ok=True)

    additional_dirs = [get_projects_dir(), plugin_dest]
    local_settings = {"permissions": {"additionalDirectories": additional_dirs}}

    if FREECAD_CMD:
        local_settings["permissions"]["allow"] = [f"Bash({FREECAD_CMD}:*)"]

    with open(local_settings_path, "w") as f:
        json.dump(local_settings, f, indent=2)
    print(f"Generated {local_settings_path}")

    print("\nRestart FreeCAD to activate the VisionCAD workbench.")

if __name__ == "__main__":
    main()
