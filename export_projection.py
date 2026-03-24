"""
Export an isometric projection of a FreeCAD model as SVG.
Reads FCSTD_PATH and SVG_PATH from a config file.
Path is passed via VISIONCAD_EXPORT_CONFIG env var, or falls back to /tmp/freecad_export_config.txt.
"""
import FreeCAD
import Part
import math
import os

print("export_projection.py starting")

import tempfile as _tf
config_path = os.environ.get("VISIONCAD_EXPORT_CONFIG",
                              os.path.join(_tf.gettempdir(), "freecad_export_config.txt"))
if not os.path.exists(config_path):
    print(f"Error: config file not found: {config_path}")
else:
    with open(config_path) as f:
        lines = f.read().strip().split("\n")
    fcstd_path = lines[0]
    svg_path = lines[1]

    print(f"Input:  {fcstd_path}")
    print(f"Output: {svg_path}")

    doc = FreeCAD.openDocument(fcstd_path)
    print(f"Objects: {[obj.Name for obj in doc.Objects]}")

    shapes = []
    for obj in doc.Objects:
        if hasattr(obj, "Shape") and obj.Shape.Edges:
            shapes.append(obj.Shape)
            print(f"  {obj.Name}: {len(obj.Shape.Edges)} edges")

    if not shapes:
        print("No shapes found")
    else:
        compound = Part.makeCompound(shapes)

        # Cabinet oblique: front face (XZ) drawn true-to-scale.
        # Depth (Y) recedes to upper-left at 45°, half scale.
        da = 0.5 * math.cos(math.radians(45))  # ~0.354

        svg_lines = []
        for edge in compound.Edges:
            verts = edge.Vertexes
            if len(verts) == 2:
                p1, p2 = verts[0].Point, verts[1].Point
                x1 = p1.x - p1.y * da
                y1 = -(p1.z) - p1.y * da
                x2 = p2.x - p2.y * da
                y2 = -(p2.z) - p2.y * da
                svg_lines.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}"/>')

        if svg_lines:
            import re
            all_x, all_y = [], []
            for line in svg_lines:
                nums = re.findall(r'"([^"]+)"', line)
                all_x.append(float(nums[0])); all_x.append(float(nums[2]))
                all_y.append(float(nums[1])); all_y.append(float(nums[3]))

            pad = 30
            vb = f"{min(all_x)-pad} {min(all_y)-pad} {max(all_x)-min(all_x)+pad*2} {max(all_y)-min(all_y)+pad*2}"

            os.makedirs(os.path.dirname(svg_path), exist_ok=True)
            with open(svg_path, "w") as f:
                f.write(f'<?xml version="1.0"?>\n')
                f.write(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}">\n')
                f.write(f'<g stroke="#333" stroke-width="1.5" fill="none" stroke-linecap="round">\n')
                f.write('\n'.join(svg_lines))
                f.write('\n</g>\n</svg>')

            print(f"Exported {len(svg_lines)} edges to {svg_path}")
        else:
            print("No edges to export")
