# Vision CAD

## Task Processing

When asked to "watch for tasks" or "process tasks", read `~/.visioncad/config.json` for `projects_dir` and poll that directory every 5 seconds for:

### Model tasks (meta.json status = "pending")
1. Set status to "generating"
2. Read meta.json for the description, and the source image if one exists (image field may be absent for text-only projects)
3. Read system_prompt_analyze.txt — produce a thorough design analysis (classification, every component, dimensions, spatial relationships). Write to `_analysis.txt` in the project dir.
4. Read system_prompt_codegen.txt — generate the FreeCAD script from the analysis. Every component in the analysis must appear in the script.
5. Write the script to the project dir as `_generated_model.py` (replace __OUTPUT_FCSTD__ and __OUTPUT_STEP__ with paths in the project dir)
6. Run: `python process_task.py <project_id> model` to execute FreeCAD and update the project

### Build tasks (meta.json build_status = "generating_queued")
1. Read cut_list.json AND `_generated_model.py` from the project
2. Study the model script for ALL joinery details — mortises, dados, rabbets, fillets, roundovers, and how parts connect to each other
3. Generate detailed build instructions (phased, shop-level) that cover every joint and shaping operation from the script — do not guess or invent operations not in the code. If a part does not have makeFillet or makeChamfer called on it, do NOT add edge profiles for that part. Only describe operations explicitly present in the script
4. Write build instructions JSON, then run: `python process_task.py <project_id> build`

## FreeCAD
- Binary: /Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd
- Use Part workbench only
- Think in inches, code in mm (x25.4)
