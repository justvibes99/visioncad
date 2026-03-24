"""
Process a single Vision CAD task. Called by the watcher loop.
Usage: python process_task.py <project_id> <mode>
  mode: "model" or "build"
"""
import json
import os
import re
import subprocess
import sys
import tempfile

from config import (
    FREECAD_CMD as FREECADCMD, PROJECT_ROOT as SCRIPT_DIR,
    PROJECTS_DIR,
)

if not FREECADCMD:
    print("Error: FreeCAD not found. Install FreeCAD or set VISIONCAD_FREECAD.", file=sys.stderr)
    sys.exit(1)


def get_project(project_id):
    with open(os.path.join(PROJECTS_DIR, project_id, "meta.json")) as f:
        return json.load(f)


def save_project(project, project_id=None):
    pid = project_id or project["id"]
    project["id"] = pid
    with open(os.path.join(PROJECTS_DIR, pid, "meta.json"), "w") as f:
        json.dump(project, f, indent=2)


def run_freecad(script_path, env=None):
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    result = subprocess.run([FREECADCMD, script_path], capture_output=True, text=True, timeout=120, env=run_env)
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


def process_model(project_id):
    project = get_project(project_id)
    proj_dir = os.path.join(PROJECTS_DIR, project_id)
    image_file = project.get("image")
    image_path = os.path.join(proj_dir, image_file) if image_file else None
    description = project.get("description", "")
    model_name = os.path.splitext(image_file)[0] if image_file else "source"

    project["status"] = "generating"
    project["log"] = "Generating 3D model..."
    save_project(project)

    # The actual Claude call happens in the parent Claude Code session
    # via the Agent tool. This script just handles the FreeCAD post-processing.
    # The agent writes _generated_model.py directly.

    script_path = os.path.join(proj_dir, "_generated_model.py")
    if not os.path.exists(script_path):
        project["status"] = "error"
        project["log"] = "No _generated_model.py found. Agent may have failed."
        save_project(project)
        return

    # Run FreeCAD
    print(f"Running FreeCAD for {project_id}...")
    output = run_freecad(script_path)

    # Extract cut list
    lines = output.split("\n")
    cut_json = None
    for i, line in enumerate(lines):
        if "CUT_JSON" in line and i + 1 < len(lines):
            cut_json = lines[i + 1].strip()
            break

    if cut_json:
        with open(os.path.join(proj_dir, "cut_list.json"), "w") as f:
            f.write(cut_json)

    # Export projection
    output_fcstd = os.path.join(proj_dir, f"{model_name}.FCStd")
    projection_svg = os.path.join(proj_dir, f"{model_name}_projection.svg")
    if os.path.exists(output_fcstd):
        run_export_projection(output_fcstd, projection_svg)

    # Update project
    project = get_project(project_id)
    project["status"] = "done"
    project["log"] = output
    project["files"] = {}
    for fname in os.listdir(proj_dir):
        if fname.endswith(".FCStd"): project["files"]["fcstd"] = fname
        elif fname.endswith(".step"): project["files"]["step"] = fname
        elif fname.endswith("_projection.svg"): project["files"]["projection_svg"] = fname
        elif fname == "cut_list.json": project["files"]["cut_list"] = fname
    save_project(project)
    print(f"Model done for {project_id}")


def process_build(project_id):
    """Generate cut sheet + build instructions PDF."""
    project = get_project(project_id)
    proj_dir = os.path.join(PROJECTS_DIR, project_id)
    image_file = project.get("image")
    model_name = os.path.splitext(image_file)[0] if image_file else "source"

    project["build_status"] = "generating"
    project["log"] = "Generating build file..."
    save_project(project)

    # Use the cut list from model generation (written by _generated_model.py).
    # It has the correct per-board breakdown (e.g., L-shapes split into individual
    # legs) that auto_cut_list.py can't derive from fused geometry.
    # Projection SVG is also from the model step — no need to redo either.

    try:
        from cutsheet import generate_svg, generate_build_svg, generate_pdf

        # Read cut list
        cut_list_path = os.path.join(proj_dir, "cut_list.json")
        with open(cut_list_path) as f:
            cut_data = json.loads(f.read())

        piece_title = project.get("name", model_name.replace("_", " ").title())

        # Find projection
        proj_svg = os.path.join(proj_dir, f"{model_name}_projection.svg")
        proj = proj_svg if os.path.exists(proj_svg) else None

        # Generate cut sheet
        cutsheet_svg = os.path.join(proj_dir, f"{model_name}_cutsheet.svg")
        path, boards, eff = generate_svg(cut_data, cutsheet_svg, projection_svg=proj, title=piece_title)
        print(f"Cut sheet: {boards} boards, {eff:.0f}% efficiency")

        # Build instructions will be generated by the agent and passed here
        # For now, just create the PDF with the cut sheet
        svg_pages = [cutsheet_svg]

        # Check if build instructions SVG was already generated by agent
        build_svg = os.path.join(proj_dir, f"{model_name}_build.svg")
        if os.path.exists(build_svg):
            svg_pages.append(build_svg)

        piece_name = project.get("name", model_name)
        pdf_path = os.path.join(proj_dir, f"{piece_name}.pdf")
        generate_pdf(svg_pages, pdf_path)

        project = get_project(project_id)
        project["build_status"] = "done"
        project["log"] = f"Build file generated. {boards} boards, {eff:.0f}% efficiency."
        for fname in os.listdir(proj_dir):
            if fname.endswith("_cutsheet.svg"): project["files"]["cutsheet_svg"] = fname
            elif fname.endswith("_build.svg"): project["files"]["build_svg"] = fname
            elif fname.endswith(".pdf"): project["files"]["pdf"] = fname
        save_project(project)
        print(f"Build file done for {project_id}")

    except Exception as e:
        import traceback
        project = get_project(project_id)
        project["build_status"] = "error"
        project["log"] = f"Error: {e}\n{traceback.format_exc()}"
        save_project(project)


if __name__ == "__main__":
    project_id = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else "model"

    if mode == "model":
        process_model(project_id)
    elif mode == "build":
        process_build(project_id)
