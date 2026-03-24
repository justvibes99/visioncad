"""
Vision CAD — generate pipeline.

Modes:
  python generate.py model <image_path> [description]   → FreeCAD model from image + projection
  python generate.py model --description "..."           → FreeCAD model from text description only
  python generate.py build <project_dir>                 → cut sheet + build instructions PDF
"""
import subprocess
import sys
import os
import re
import json
import tempfile
from cutsheet import generate_svg, generate_build_svg, generate_pdf

from config import (
    FREECAD_CMD as FREECADCMD, CLAUDE_CMD, PROJECT_ROOT as SCRIPT_DIR,
    SYSTEM_PROMPT_FILE, ANALYZE_PROMPT_FILE, CODEGEN_PROMPT_FILE,
    PROJECTS_DIR as DEFAULT_OUTPUT_DIR,
)

if not FREECADCMD:
    print("Error: FreeCAD not found. Install FreeCAD or set VISIONCAD_FREECAD.", file=sys.stderr)
    sys.exit(1)
if not CLAUDE_CMD:
    print("Error: Claude CLI not found. Install with: npm install -g @anthropic-ai/claude-code", file=sys.stderr)
    sys.exit(1)


def run_claude(prompt, system_prompt_file=None, timeout=900):
    import tempfile
    cmd = [CLAUDE_CMD, "-p", "--no-session-persistence"]
    if system_prompt_file:
        cmd += ["--system-prompt-file", system_prompt_file]
    cmd.append(prompt)

    # Write output to temp files instead of capturing via pipes (avoids buffering hang)
    with tempfile.NamedTemporaryFile(mode='w+', suffix='.txt', delete=False) as out_f, \
         tempfile.NamedTemporaryFile(mode='w+', suffix='.txt', delete=False) as err_f:
        out_path, err_path = out_f.name, err_f.name
        proc = subprocess.Popen(cmd, stdout=out_f, stderr=err_f, stdin=subprocess.DEVNULL)

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        os.unlink(out_path)
        os.unlink(err_path)
        raise

    with open(out_path) as f:
        stdout = f.read()
    with open(err_path) as f:
        stderr = f.read()
    os.unlink(out_path)
    os.unlink(err_path)

    if proc.returncode != 0:
        print(f"Claude CLI error:\n{stderr}", file=sys.stderr)
        sys.exit(1)
    return stdout


def run_freecad(script_path, env=None):
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    result = subprocess.run([FREECADCMD, script_path], capture_output=True, text=True, timeout=60, env=run_env)
    return result.stdout + result.stderr


def run_export_projection(fcstd_path, svg_path):
    """Export projection using a unique temp config file."""
    fd, config_path = tempfile.mkstemp(suffix='.txt', prefix='visioncad_export_')
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(f"{fcstd_path}\n{svg_path}\n")
        run_freecad(os.path.join(SCRIPT_DIR, "export_projection.py"),
                    env={"VISIONCAD_EXPORT_CONFIG": config_path})
    finally:
        os.unlink(config_path)


def extract_python(text):
    match = re.search(r"```python\s*\n(.*?)```", text, re.DOTALL)
    return match.group(1).strip() if match else text.strip()


def generate_model(image_paths, description, output_dir):
    """Step 1: Image(s) and/or description → FreeCAD model + projection SVG.
    image_paths: single path string, list of paths, or None."""
    os.makedirs(output_dir, exist_ok=True)
    model_name = "source"

    # Normalize image_paths to a list
    if isinstance(image_paths, str):
        image_paths = [image_paths]
    elif not image_paths:
        image_paths = []

    output_fcstd = os.path.join(output_dir, f"{model_name}.FCStd")
    output_step = os.path.join(output_dir, f"{model_name}.step")

    # Pass 1: Analyze the piece
    image_prefix = " ".join(os.path.abspath(p) for p in image_paths)
    if image_paths and description:
        analyze_prompt = f"{image_prefix} Analyze this piece of furniture. Additional context: {description}"
    elif image_paths:
        analyze_prompt = f"{image_prefix} Analyze this piece of furniture. Estimate reasonable dimensions."
    else:
        analyze_prompt = f"Analyze this piece of furniture from the following description (no reference image): {description}"

    print("Analyzing design...")
    analysis = run_claude(analyze_prompt, system_prompt_file=ANALYZE_PROMPT_FILE)

    # Save analysis for reference
    with open(os.path.join(output_dir, "_analysis.txt"), "w") as f:
        f.write(analysis)
    print("Analysis complete.")

    # Pass 2: Generate FreeCAD script from the analysis
    codegen_prompt = f"Generate the FreeCAD Python script for this furniture piece.\n\nDESIGN ANALYSIS:\n{analysis}"

    print("Generating FreeCAD script...")
    response = run_claude(codegen_prompt, system_prompt_file=CODEGEN_PROMPT_FILE)
    script = extract_python(response)
    script = script.replace("__OUTPUT_FCSTD__", output_fcstd)
    script = script.replace("__OUTPUT_STEP__", output_step)

    script_path = os.path.join(output_dir, "_generated_model.py")
    with open(script_path, "w") as f:
        f.write(script)
    print(f"Generated script: {script_path}")

    # Run in FreeCAD
    print("Building model in FreeCAD...")
    output = run_freecad(script_path)

    # Extract cut list JSON — find line after CUT_JSON marker
    cut_json = None
    lines = output.split("\n")
    for i, line in enumerate(lines):
        if "CUT_JSON" in line and i + 1 < len(lines):
            cut_json = lines[i + 1].strip()
            break

    if cut_json:
        with open(os.path.join(output_dir, "cut_list.json"), "w") as f:
            f.write(cut_json)
        print("Saved cut_list.json")

    # Export projection SVG
    projection_svg = os.path.join(output_dir, f"{model_name}_projection.svg")
    run_export_projection(output_fcstd, projection_svg)

    # Print summary
    for line in output.split("\n"):
        if any(k in line for k in ["CUT LIST", "---", "Saved", "Objects"]) or (cut_json is None and line.strip()):
            print(line)

    print(f"\nModel: {output_fcstd}")
    return output_fcstd


def generate_build_file(project_dir, description=""):
    """Step 2: Read cut list from project → generate cut sheet + build instructions → PDF."""
    cut_list_path = os.path.join(project_dir, "cut_list.json")
    if not os.path.exists(cut_list_path):
        print("Error: No cut_list.json found. Generate the model first.")
        sys.exit(1)

    with open(cut_list_path) as f:
        cut_json = f.read()
    cut_data = json.loads(cut_json)
    if isinstance(cut_data, dict):
        cut_data = cut_data.get("cut_list", [])

    model_name = "source"
    # Project title from meta.json or directory name
    meta_path = os.path.join(project_dir, "meta.json")
    if os.path.exists(meta_path):
        with open(meta_path) as mf:
            piece_title = json.load(mf).get("name", os.path.basename(project_dir))
    else:
        piece_title = os.path.basename(project_dir).replace("_", " ").replace("-", " ").title()

    # Find projection SVG
    proj_svg = os.path.join(project_dir, f"{model_name}_projection.svg")
    proj = proj_svg if os.path.exists(proj_svg) else None

    # Generate cut sheet SVG
    cutsheet_svg = os.path.join(project_dir, f"{model_name}_cutsheet.svg")
    print("Generating cut sheet...")
    path, boards, eff = generate_svg(cut_data, cutsheet_svg, projection_svg=proj, title=piece_title)
    print(f"Cut sheet: {path} ({boards} boards, {eff:.0f}% efficiency)")

    # Generate build instructions via Claude
    print("Generating build instructions...")

    # Include design analysis from the model script for joinery context
    design_context = ""
    model_script_path = os.path.join(project_dir, "_generated_model.py")
    if os.path.exists(model_script_path):
        with open(model_script_path) as f:
            script_text = f.read()
        # Extract everything before the first executable code (comments + docstrings)
        # Also grab any inline comments about joinery, mortises, fillets, etc.
        design_context = f"\n\nModel script (contains design analysis, dimensions, joinery details):\n{script_text}"

    build_prompt = f"""Given this furniture project, generate detailed build instructions.

Project: {description or piece_title}

Cut list:
{cut_json}
{design_context}

Generate a JSON object with two keys:

1. "materials" — list of non-lumber materials needed. Each item has "item" (string) and "qty" (string). Include:
   - Glue (type and estimated amount)
   - Fasteners (screws, nails, dowels, biscuits — specific sizes and counts)
   - Hardware (handles, pulls, hinges, slides — from the model)
   - Finish (type and estimated amount)
   - Anything else needed (sandpaper grits, wood filler, etc.)
   Only include materials actually required by this specific build.

2. "phases" — array of build phases covering the COMPLETE build process. Each phase has "phase" (numbered title like "1. MILLING & DIMENSIONING") and "steps" (list of detailed instruction strings).

IMPORTANT: Read the model script carefully for ALL joinery details — mortises, dados, rabbets, fillets, roundovers, glue-ups, and how parts connect. The build instructions MUST include every joint and shaping operation from the script. Do not guess or invent operations that are not in the code. If a part does not have makeFillet or makeChamfer called on it, do NOT add edge profiles for that part. Only describe operations that are explicitly present in the script.

Include specific dimensions, tool/bit sizes, clamp times, measurement checks, and tips.
Write for a competent DIYer who uses S4S lumber and owns standard shop tools (table saw, miter saw, router, drill, clamps).
Return ONLY the JSON object wrapped in ```json ... ```. No other text."""

    build_response = run_claude(build_prompt)
    build_svg = os.path.join(project_dir, f"{model_name}_build.svg")

    svg_pages = [cutsheet_svg]
    try:
        match = re.search(r'```json\s*\n(.*?)```', build_response, re.DOTALL)
        if match:
            build_data = json.loads(match.group(1))
        else:
            build_clean = re.search(r'[\[{].*[\]}]', build_response, re.DOTALL)
            build_data = json.loads(build_clean.group()) if build_clean else {}

        # Handle both formats: new {materials, phases} or legacy [phases]
        if isinstance(build_data, list):
            build_instructions = build_data
            materials = []
        else:
            build_instructions = build_data.get("phases", [])
            materials = build_data.get("materials", [])

        if build_instructions:
            generate_build_svg(build_instructions, build_svg, title=piece_title, materials=materials)
            svg_pages.append(build_svg)
            print(f"Build instructions: {build_svg}")
    except (json.JSONDecodeError, Exception) as e:
        print(f"Warning: Could not parse build instructions: {e}")

    # Combine into PDF — name after the project, not the image
    pdf_path = os.path.join(project_dir, f"{piece_title}.pdf")
    generate_pdf(svg_pages, pdf_path)
    print(f"PDF: {pdf_path}")
    return pdf_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python generate.py model <image_path> [description]")
        print("  python generate.py build <project_dir> [description]")
        sys.exit(1)

    mode = sys.argv[1]

    # Parse --output-dir flag if present
    args = sys.argv[2:]
    output_dir_override = None
    if "--output-dir" in args:
        idx = args.index("--output-dir")
        output_dir_override = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    if mode == "model":
        # Support: model <images...> [--description "..."]
        image_paths = []
        description = ""
        if "--description" in args:
            idx = args.index("--description")
            description = " ".join(args[idx + 1:])
            args = args[:idx]
        image_paths = args  # remaining args are image paths
        if not image_paths and not description:
            print("Error: provide image path(s) or --description \"...\"")
            sys.exit(1)
        output_dir = output_dir_override or DEFAULT_OUTPUT_DIR
        generate_model(image_paths, description, output_dir)
    elif mode == "build":
        project_dir = args[0]
        description = " ".join(args[1:]) if len(args) > 1 else ""
        generate_build_file(project_dir, description)
    else:
        print(f"Unknown mode: {mode}")
        sys.exit(1)
