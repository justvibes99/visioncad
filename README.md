<div align="center">

<img src="docs/banner.png" alt="VisionCAD" width="100%">

</div>

Turn a photo or description of furniture into a 3D model, iterate on the design with Claude, and generate cut sheets and build instructions — powered by Claude and FreeCAD.

## What it does

1. **Analyze** — Claude studies your reference image(s) or text description
2. **Model** — Generates a FreeCAD Python script and builds the 3D model
3. **Cut Sheet** — Derives a cut list from the geometry, lays out boards, shows composites
4. **Build Instructions** — Claude writes phased shop instructions with a materials list

## Prerequisites

- **FreeCAD 1.0+** — [freecad.org](https://www.freecad.org/downloads.php)
- **Claude CLI** — `npm install -g @anthropic-ai/claude-code` (requires Anthropic account)
- **Python 3.10+** with pip
- **Cairo** (for PDF generation) — `brew install cairo` (macOS) or `apt install libcairo2-dev` (Linux)

## Install

```bash
git clone https://github.com/justvibes99/visioncad.git
cd visioncad

# Python dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# FreeCAD plugin
python install_plugin.py
```

Restart FreeCAD after installing — the **Vision CAD** workbench will appear in the workbench dropdown.

## Usage

### FreeCAD Plugin (recommended)

1. Open FreeCAD, switch to the **Vision CAD** workbench
2. Click **New Project** in the side panel
3. Enter a name, optionally load reference image(s), add build notes
4. The model generates automatically — iterate via the chat panel
5. Click **Create Docs** to generate the cut sheet and build instructions PDF

### CLI

```bash
# From image(s)
python generate.py model photo.jpg
python generate.py model front.jpg side.jpg --description "48 inch wide, walnut"

# From description only
python generate.py model --description "Simple Shaker end table, 24x18x26 inches"

# Generate build docs for an existing project
python generate.py build /path/to/project/dir
```

## Configuration

VisionCAD looks for settings in this order:

1. **Environment variables** — `VISIONCAD_FREECAD`, `VISIONCAD_CLAUDE`, `VISIONCAD_PROJECTS`, `VISIONCAD_ROOT`
2. **Config file** — `~/.visioncad/config.json`
3. **Auto-detection** — searches PATH and common install locations

Example `~/.visioncad/config.json`:
```json
{
  "projects_dir": "/Users/you/Documents/VisionCAD",
  "freecad_path": "/usr/bin/freecadcmd",
  "claude_path": "/usr/local/bin/claude"
}
```

Most users won't need a config file — the defaults work if FreeCAD and Claude are on your PATH.

## Project Structure

Each project lives in your projects directory:
```
MyProject/
  meta.json              — project metadata
  source.png             — reference image(s)
  _analysis.txt          — design analysis from Claude
  _generated_model.py    — FreeCAD Python script
  source.FCStd           — 3D model
  source.step            — STEP export
  source_projection.svg  — cabinet oblique projection
  source_cutsheet.svg    — cut sheet with board layouts
  source_build.svg       — build instructions
  MyProject.pdf          — combined document
```

## How It Works

The model generation is two passes:
1. **Analysis pass** — Claude produces a detailed breakdown: piece classification, every component, proportions, dimensions, structural requirements, edge treatments
2. **Code generation pass** — Claude writes FreeCAD Python from the analysis, modeling every component

The cut list is derived from the actual FCStd geometry (not hardcoded in the script), so it stays accurate as you iterate on the model.
