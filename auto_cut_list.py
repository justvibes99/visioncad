"""
Auto-generate a cut list from a FreeCAD model file.
Inspects the actual geometry — no hardcoded data.

Usage: freecadcmd auto_cut_list.py <fcstd_path>
Outputs CUT_JSON to stdout.
"""
import FreeCAD
import Part
import json
import sys
import os
import math

# Standard lumber: thickness_mm → [(max_width_mm, stock_name), ...] sorted by width
STOCK_TABLE = {
    19: [  # 3/4" thick (1x)
        (38, "1x2"), (64, "1x3"), (89, "1x4"), (140, "1x6"),
        (184, "1x8"), (286, "1x12"),
    ],
    38: [  # 1-1/2" thick (2x)
        (89, "2x4"), (140, "2x6"), (184, "2x8"),
    ],
    89: [  # 3-1/2" thick (4x)
        (89, "4x4"),
    ],
}

# Width labels in inches
WIDTH_LABELS = {
    38: '1-1/2"', 64: '2-1/2"', 89: '3-1/2"', 140: '5-1/2"',
    184: '7-1/4"', 235: '9-1/4"', 286: '11-1/4"',
}


def mm_to_in_str(mm):
    inches = mm / 25.4
    whole = int(inches)
    frac = inches - whole
    sixteenths = round(frac * 16)
    if sixteenths == 16:
        whole += 1
        sixteenths = 0
    if sixteenths == 0:
        return f'{whole}"' if whole > 0 else '0"'
    from math import gcd
    g = gcd(sixteenths, 16)
    num, den = sixteenths // g, 16 // g
    return f'{whole}-{num}/{den}"' if whole > 0 else f'{num}/{den}"'


def detect_thickness(shape):
    """Detect lumber thickness from the shape.
    For rectangular parts, use bounding box smallest dim.
    For composite parts (L-shape, etc.), default to 3/4" (19mm)
    since furniture composites are almost always made from 1x stock."""
    bb = shape.BoundBox
    dims = sorted([bb.XLength, bb.YLength, bb.ZLength])
    bbox_vol = dims[0] * dims[1] * dims[2]
    fill = shape.Volume / bbox_vol if bbox_vol > 0 else 0

    if fill > 0.85:
        # Rectangular — bounding box smallest dim is the thickness
        thin = dims[0]
        for std in [19, 38, 89]:
            if abs(thin - std) <= 3:
                return std
        return min([19, 38, 89], key=lambda s: abs(s - thin))
    else:
        # Composite shape (L, U, T, etc.) — made from joined boards
        # Nearly all furniture composites use 3/4" (1x) stock
        # Exception: if smallest dim is clearly 1-1/2" range
        if abs(dims[0] - 38) <= 3:
            return 38
        return 19


def classify_part(shape):
    """Classify a part and return (thickness_mm, width_mm, length_mm, is_composite)."""
    bb = shape.BoundBox
    dims = sorted([bb.XLength, bb.YLength, bb.ZLength])
    thickness = detect_thickness(shape)
    bbox_vol = dims[0] * dims[1] * dims[2]
    fill = shape.Volume / bbox_vol if bbox_vol > 0 else 0
    is_composite = fill < 0.85

    if not is_composite:
        # Rectangular: remove thickness dim, remainder is width x length
        remaining = sorted(dims)
        diffs = [(abs(d - thickness), i) for i, d in enumerate(remaining)]
        diffs.sort()
        t_idx = diffs[0][1]
        wl = sorted([d for i, d in enumerate(remaining) if i != t_idx])
        return thickness, wl[0], wl[1], False
    else:
        # Composite: the two largest bbox dims are the face dimensions
        return thickness, dims[1], dims[2], True


def decompose_composite(shape, thickness_mm):
    """Decompose a composite shape (L, T, U) into individual rectangular boards.

    Strategy: find the large flat side faces — each one corresponds to a board.
    The face's bounding box gives us the board's width and length.
    Filter out small fillet faces and profile end-cap faces.

    Returns a list of (width_mm, length_mm) tuples, or None if decomposition fails.
    """
    bb = shape.BoundBox
    total_area = sum(f.Area for f in shape.Faces)

    # Collect all large flat faces (area > 5% of total, oriented along a principal axis)
    big_faces = []
    for face in shape.Faces:
        if face.Area < total_area * 0.05:
            continue
        n = face.normalAt(0, 0)
        # Must be aligned to a principal axis
        if not (abs(abs(n.x) - 1) < 0.05 or abs(abs(n.y) - 1) < 0.05 or abs(abs(n.z) - 1) < 0.05):
            continue
        fbb = face.BoundBox
        fdims = sorted([fbb.XLength, fbb.YLength, fbb.ZLength])
        # Must be flat (one dim ~0) and substantial
        if fdims[0] > 1:
            continue
        big_faces.append({
            "normal": (round(n.x), round(n.y), round(n.z)),
            "area": face.Area,
            "width": fdims[1],
            "length": fdims[2],
        })

    if len(big_faces) < 2:
        return None

    # Group faces by normal direction (opposite normals = same board, two sides)
    from collections import defaultdict
    groups = defaultdict(list)
    for f in big_faces:
        # Normalize direction (treat +X and -X as same)
        key = tuple(abs(v) for v in f["normal"])
        groups[key].append(f)

    # Each group with 2 faces (front+back of a board) is one board
    # Take the largest face in each group for dimensions
    boards = []
    for key, faces in groups.items():
        best = max(faces, key=lambda f: f["area"])
        w, l = best["width"], best["length"]
        # Skip if this looks like a profile end-cap (both dims are large
        # relative to bbox — that's the L/T/U profile, not a board face)
        if w > thickness_mm * 3 and l > thickness_mm * 3:
            # Check if this is a side face or a profile face
            # Profile faces have area much less than w*l (L-shape cutout)
            fill = best["area"] / (w * l) if w * l > 0 else 0
            if fill < 0.8:
                # This is a profile end-cap face (L/T/U shaped), skip it
                continue
        boards.append((min(w, l), max(w, l)))

    if len(boards) < 2:
        return None

    # Deduplicate boards that are nearly identical (front and back of same board
    # may appear as separate groups if normals differ slightly)
    unique = []
    for bw, bl in boards:
        is_dup = False
        for uw, ul in unique:
            if abs(bw - uw) < 3 and abs(bl - ul) < 3:
                is_dup = True
                break
        if not is_dup:
            unique.append((bw, bl))

    if len(unique) < 2:
        return None

    return unique


def pick_stock(thickness_mm, width_mm):
    """Pick the appropriate stock size for given thickness and width."""
    stocks = STOCK_TABLE.get(thickness_mm)
    if not stocks:
        # Find closest thickness
        thickness_mm = min(STOCK_TABLE.keys(), key=lambda t: abs(t - thickness_mm))
        stocks = STOCK_TABLE[thickness_mm]

    notes = []
    for max_w, name in stocks:
        if width_mm <= max_w + 2:  # 2mm tolerance
            return name, notes

    # Check if the largest stock can be ripped to width (overage < 1")
    largest_w, largest_name = stocks[-1]
    overage = width_mm - largest_w
    if overage <= 25:  # within ~1" — just rip the widest stock
        return largest_name, [f"rip to {mm_to_in_str(width_mm)}"]

    # Need a real glue-up. Pick the stock that needs the fewest boards.
    best = None
    for max_w, name in stocks:
        n = math.ceil(width_mm / max_w)
        waste = n * max_w - width_mm
        if best is None or n < best[0] or (n == best[0] and waste < best[1]):
            best = (n, waste, name)

    stock_name = best[2] if best else largest_name
    return stock_name, [f"glue-up to {mm_to_in_str(width_mm)} wide"]


def generate_cut_list(fcstd_path):
    """Generate a cut list from a FreeCAD model file."""
    doc = FreeCAD.openDocument(fcstd_path)

    cuts = []
    seen_names = set()

    for obj in doc.Objects:
        # Skip groups and objects without geometry
        if not hasattr(obj, "Shape") or not obj.Shape.Edges:
            continue
        # Skip if this is a group (App::DocumentObjectGroup)
        if obj.TypeId in ("App::DocumentObjectGroup",):
            continue
        # Skip if bounding box is just a container for other objects
        # (groups that have Shape because they contain shapes)
        if hasattr(obj, "Group") and obj.Group:
            continue

        name = obj.Label or obj.Name
        if name in seen_names:
            continue
        seen_names.add(name)

        thickness, width, length, is_composite = classify_part(obj.Shape)

        if is_composite:
            # Try to decompose into individual boards
            boards = decompose_composite(obj.Shape, thickness)
            if boards:
                for idx, (bw, bl) in enumerate(boards):
                    stock, stock_notes = pick_stock(thickness, bw)
                    notes = list(stock_notes)
                    suffix = f" (piece {idx+1}/{len(boards)})"
                    cuts.append({
                        "part": name + suffix,
                        "stock": stock,
                        "cut_length": round(bl),
                        "cut_length_in": mm_to_in_str(bl),
                        "qty": 1,
                        "notes": "; ".join(notes),
                    })
                continue
            # Decomposition failed — fall through to single entry
            # with bounding-box dimensions as best guess

        stock_a, notes_a = pick_stock(thickness, width)
        needs_glueup_a = any("glue-up" in n for n in notes_a)

        # For panels needing a glue-up, check if flipping the orientation
        # (cut along the shorter dimension, glue along the longer) uses less stock.
        if needs_glueup_a and not is_composite:
            stock_b, notes_b = pick_stock(thickness, length)
            needs_glueup_b = any("glue-up" in n for n in notes_b)

            # Compare total board-feet for each orientation
            stocks = STOCK_TABLE.get(thickness, STOCK_TABLE.get(19))
            sw_a = dict((n, w) for w, n in stocks).get(stock_a, width)
            sw_b = dict((n, w) for w, n in stocks).get(stock_b, length)
            boards_a = math.ceil(width / sw_a)
            boards_b = math.ceil(length / sw_b) if needs_glueup_b else 1
            total_a = boards_a * length  # total linear stock, orientation A
            total_b = boards_b * width   # total linear stock, orientation B

            if total_b < total_a * 0.7:  # only flip if it saves >30%
                # Flipped orientation is more efficient
                stock = stock_b
                notes = list(notes_b) if needs_glueup_b else []
                if needs_glueup_b:
                    # Rewrite note: gluing along the length
                    notes = [f"glue-up to {mm_to_in_str(length)} long"]
                cuts.append({
                    "part": name,
                    "stock": stock,
                    "cut_length": round(width),
                    "cut_length_in": mm_to_in_str(width),
                    "qty": 1,
                    "notes": "; ".join(notes),
                })
                continue

        notes = list(notes_a)
        if is_composite:
            notes.append("composite — could not auto-decompose")

        cuts.append({
            "part": name,
            "stock": stock_a,
            "cut_length": round(length),
            "cut_length_in": mm_to_in_str(length),
            "qty": 1,
            "notes": "; ".join(notes),
        })

    return cuts


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: freecadcmd auto_cut_list.py <fcstd_path>")
        sys.exit(1)

    fcstd_path = sys.argv[1]
    cuts = generate_cut_list(fcstd_path)

    print("\n=== CUT_JSON ===")
    print(json.dumps(cuts))
