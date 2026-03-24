"""
Cut sheet generator — professional engineering shop drawing style.
Outputs an SVG formatted like a printed technical drawing.
"""
import json
import os

KERF = 3  # mm (1/8")

STOCK_LENGTHS = [
    (1829, '6\''), (2438, '8\''), (3048, '10\''), (3658, '12\''),
]

# Actual dims in inches (thickness, width)
STOCK_DIMS_IN = {
    "1x2": ('3/4"', '1-1/2"'), "1x3": ('3/4"', '2-1/2"'), "1x4": ('3/4"', '3-1/2"'),
    "1x6": ('3/4"', '5-1/2"'), "1x8": ('3/4"', '7-1/4"'), "1x10": ('3/4"', '9-1/4"'),
    "1x12": ('3/4"', '11-1/4"'),
    "2x2": ('1-1/2"', '1-1/2"'), "2x3": ('1-1/2"', '2-1/2"'), "2x4": ('1-1/2"', '3-1/2"'),
    "2x6": ('1-1/2"', '5-1/2"'), "2x8": ('1-1/2"', '7-1/4"'), "2x10": ('1-1/2"', '9-1/4"'),
    "2x12": ('1-1/2"', '11-1/4"'),
    "4x4": ('3-1/2"', '3-1/2"'),
}

# Same in mm for scaling drawings
STOCK_DIMS_MM = {
    "1x2": (19, 38), "1x3": (19, 64), "1x4": (19, 89), "1x6": (19, 140),
    "1x8": (19, 184), "1x10": (19, 235), "1x12": (19, 286),
    "2x2": (38, 38), "2x3": (38, 64), "2x4": (38, 89), "2x6": (38, 140),
    "2x8": (38, 184), "2x10": (38, 235), "2x12": (38, 286),
    "4x4": (89, 89),
}


def mm_to_in_str(mm):
    """Convert mm to a fractional inch string (nearest 1/16)."""
    inches = mm / 25.4
    whole = int(inches)
    frac = inches - whole
    # Round to nearest 1/16
    sixteenths = round(frac * 16)
    if sixteenths == 16:
        whole += 1
        sixteenths = 0
    if sixteenths == 0:
        return f'{whole}"' if whole > 0 else '0"'
    # Simplify fraction
    from math import gcd
    g = gcd(sixteenths, 16)
    num, den = sixteenths // g, 16 // g
    if whole > 0:
        return f'{whole}-{num}/{den}"'
    return f'{num}/{den}"'

# Engineering drawing palette
C = {
    "bg": "#ffffff",
    "line": "#222222",
    "line_light": "#999999",
    "line_dim": "#555555",
    "fill_part": "#e8e4df",
    "fill_waste": "#f5f5f5",
    "fill_header": "#f0eeeb",
    "text": "#222222",
    "text_dim": "#444444",
    "text_light": "#888888",
    "accent": "#c0392b",
    "border": "#222222",
}

FONT = "'IBM Plex Mono', 'SF Mono', 'Consolas', monospace"
FONT_SANS = "'IBM Plex Sans', 'Helvetica Neue', 'Arial', sans-serif"

# Sheet size — match US Letter at 96 DPI for 1:1 PDF rendering
SHEET_W = 816   # 8.5" * 96
SHEET_H = 1056  # 11" * 96
MARGIN = 36
CONTENT_W = SHEET_W - MARGIN * 2


def _page_break_if_needed(y, needed_height):
    """If the next content block won't fit on the current page, advance y
    to the top of the next page. Returns the (possibly advanced) y."""
    page_num = int(y // SHEET_H)
    current_page_bottom = (page_num + 1) * SHEET_H - MARGIN
    if y + needed_height > current_page_bottom:
        return (page_num + 1) * SHEET_H + MARGIN
    return y


def _add_page_borders(els, total_height):
    """Add per-page border rectangles."""
    num_pages = max(1, int((total_height + SHEET_H - 1) // SHEET_H))
    for p in range(num_pages):
        py = p * SHEET_H
        ph = min(SHEET_H, total_height - py)
        els.append(
            f'<rect x="8" y="{py + 8}" width="{SHEET_W - 16}" height="{ph - 16}" '
            f'fill="none" stroke="{C["border"]}" stroke-width="2"/>'
        )
        els.append(
            f'<rect x="12" y="{py + 12}" width="{SHEET_W - 24}" height="{ph - 24}" '
            f'fill="none" stroke="{C["border"]}" stroke-width="0.5"/>'
        )


def pick_stock_length(cuts):
    """Pick the best stock length for a set of cuts.
    Defaults to 8'. Uses 6' if it doesn't need more boards.
    Only goes to 10'/12' if cuts physically don't fit in 8'.
    cuts: list of (name, length_mm) tuples for one stock type."""
    max_cut = max(length for _, length in cuts)

    # Find shortest stock that fits the longest cut
    min_length = None
    for length_mm, label in STOCK_LENGTHS:
        if length_mm >= max_cut:
            min_length = (length_mm, label)
            break
    if min_length is None:
        return max_cut, mm_to_in_str(max_cut)

    # If cuts require longer than 8', use the minimum that fits
    EIGHT_FT = 2438
    if max_cut > EIGHT_FT:
        return min_length

    # Default to 8'
    boards_8 = bin_pack(cuts, EIGHT_FT)

    # Check if 6' works just as well
    SIX_FT = 1829
    if max_cut <= SIX_FT:
        boards_6 = bin_pack(cuts, SIX_FT)
        if len(boards_6) <= len(boards_8):
            return SIX_FT, "6'"

    return EIGHT_FT, "8'"


def bin_pack(cuts, stock_length):
    sorted_cuts = sorted(cuts, key=lambda c: c[1], reverse=True)
    boards = []
    remaining = []
    for name, length in sorted_cuts:
        placed = False
        for i, space in enumerate(remaining):
            if length + KERF <= space:
                boards[i].append((name, length))
                remaining[i] -= (length + KERF)
                placed = True
                break
        if not placed:
            boards.append([(name, length)])
            remaining.append(stock_length - length - KERF)
    return boards


def _esc(text):
    """Escape XML special characters."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _dim_line_h(els, x1, x2, y, label, above=True):
    """Horizontal dimension line with ticks and centered label."""
    tick = 6
    label_y = y - 8 if above else y + 14
    tick_y1 = y - tick if above else y
    tick_y2 = y if above else y + tick
    els.append(f'<line x1="{x1}" y1="{y}" x2="{x2}" y2="{y}" stroke="{C["line_dim"]}" stroke-width="0.5"/>')
    els.append(f'<line x1="{x1}" y1="{tick_y1}" x2="{x1}" y2="{tick_y2}" stroke="{C["line_dim"]}" stroke-width="0.5"/>')
    els.append(f'<line x1="{x2}" y1="{tick_y1}" x2="{x2}" y2="{tick_y2}" stroke="{C["line_dim"]}" stroke-width="0.5"/>')
    mid = (x1 + x2) / 2
    # White knockout behind text
    tw = len(str(label)) * 6.5 + 8
    els.append(f'<rect x="{mid - tw/2}" y="{label_y - 9}" width="{tw}" height="13" fill="{C["bg"]}"/>')
    els.append(
        f'<text x="{mid}" y="{label_y}" font-family="{FONT}" font-size="9" '
        f'fill="{C["text_dim"]}" text-anchor="middle">{_esc(label)}</text>'
    )


def _dim_line_v(els, x, y1, y2, label, right=True):
    """Vertical dimension line with ticks and label."""
    tick = 6
    label_x = x + 10 if right else x - 10
    tick_x1 = x if right else x - tick
    tick_x2 = x + tick if right else x
    els.append(f'<line x1="{x}" y1="{y1}" x2="{x}" y2="{y2}" stroke="{C["line_dim"]}" stroke-width="0.5"/>')
    els.append(f'<line x1="{tick_x1}" y1="{y1}" x2="{tick_x2}" y2="{y1}" stroke="{C["line_dim"]}" stroke-width="0.5"/>')
    els.append(f'<line x1="{tick_x1}" y1="{y2}" x2="{tick_x2}" y2="{y2}" stroke="{C["line_dim"]}" stroke-width="0.5"/>')
    mid = (y1 + y2) / 2
    anchor = "start" if right else "end"
    els.append(
        f'<text x="{label_x}" y="{mid + 3}" font-family="{FONT}" font-size="9" '
        f'fill="{C["text_dim"]}" text-anchor="{anchor}">{_esc(label)}</text>'
    )


def _embed_projection_svg(els, svg_path, area_x, area_y, area_w, area_h):
    """
    Embed a FreeCAD-exported projection SVG into the cut sheet.
    Reads the SVG file, extracts the line elements, and rescales them to fit the area.
    """
    import re

    if not os.path.exists(svg_path):
        return

    with open(svg_path) as f:
        content = f.read()

    # Extract all line elements
    lines = re.findall(
        r'<line x1="([^"]+)" y1="([^"]+)" x2="([^"]+)" y2="([^"]+)"',
        content
    )
    # Extract all path elements
    paths = re.findall(r'<path d="([^"]+)"', content)

    if not lines and not paths:
        return

    # Calculate bounding box from lines
    all_x, all_y = [], []
    for x1, y1, x2, y2 in lines:
        all_x.extend([float(x1), float(x2)])
        all_y.extend([float(y1), float(y2)])

    if not all_x:
        return

    min_x, max_x = min(all_x), max(all_x)
    min_y, max_y = min(all_y), max(all_y)
    src_w = max_x - min_x or 1
    src_h = max_y - min_y or 1

    # Scale to fit area with padding
    pad = 12
    scale = min((area_w - pad * 2) / src_w, (area_h - pad * 2) / src_h)
    off_x = area_x + (area_w - src_w * scale) / 2 - min_x * scale
    off_y = area_y + (area_h - src_h * scale) / 2 - min_y * scale

    for x1, y1, x2, y2 in lines:
        sx1 = off_x + float(x1) * scale
        sy1 = off_y + float(y1) * scale
        sx2 = off_x + float(x2) * scale
        sy2 = off_y + float(y2) * scale
        els.append(
            f'<line x1="{sx1:.1f}" y1="{sy1:.1f}" x2="{sx2:.1f}" y2="{sy2:.1f}" '
            f'stroke="{C["line"]}" stroke-width="0.6" stroke-linecap="round"/>'
        )


def _parse_inches(s):
    """Parse a fractional inch string like '12', '11-1/4', '13-7/8' to float."""
    parts = s.replace('-', ' ').split()
    total = 0
    for p in parts:
        if '/' in p:
            num, den = p.split('/')
            total += float(num) / float(den)
        else:
            total += float(p)
    return total


def _expand_glueups(item):
    """Determine if a BOM item is a glue-up and how many boards it needs.

    Two patterns exist in the data:
    1. qty=4, "edge-glue for 29" wide" → qty IS the board count already
    2. qty=1, "glue-up to 12" wide"   → needs expansion to N boards

    Returns (boards_needed, target_width_mm) or (None, None) if no glue-up.
    boards_needed is the TOTAL board count (not a multiplier on qty).
    """
    import re, math
    notes = item.get("notes", "")
    stock = item["stock"]
    qty = item.get("qty", 1)
    stock_width_mm = STOCK_DIMS_MM.get(stock, (0, 0))[1]
    if stock_width_mm <= 0:
        return None, None

    target_mm = None

    # "glue-up to 12" wide" — from auto_cut_list.py (typically qty=1)
    match = re.search(r'glue-up to (\d+(?:[.-]\d+/?(?:\d+)?)?)["\u2033]?\s*\w*', notes, re.IGNORECASE)
    if match:
        target_mm = _parse_inches(match.group(1)) * 25.4

    # "edge-glue for 29" wide top" — from model scripts
    if target_mm is None:
        match = re.search(r'edge-glue\s+(?:for|to)\s+(\d+(?:[.-]\d+/?(?:\d+)?)?)["\u2033]?\s*wide', notes, re.IGNORECASE)
        if match:
            target_mm = _parse_inches(match.group(1)) * 25.4

    # "edge-glue" with no width — qty already represents boards
    if target_mm is None and re.search(r'edge-glue', notes, re.IGNORECASE):
        if qty > 1:
            target_mm = qty * stock_width_mm
            return qty, target_mm
        return None, None

    # "13-7/8" face" dimension that exceeds stock width
    if target_mm is None:
        face_match = re.search(r'(\d+(?:-\d+/\d+)?)["\u2033]?\s*face', notes, re.IGNORECASE)
        if face_match:
            face_mm = _parse_inches(face_match.group(1)) * 25.4
            if face_mm > stock_width_mm + 2:  # 2mm tolerance
                target_mm = face_mm

    if target_mm is None:
        return None, None

    # Add tolerance for rounding (a board can contribute its full width minus ~1mm for joint loss)
    boards_needed = math.ceil((target_mm - 2) / stock_width_mm)
    if boards_needed <= 1:
        return None, None

    return boards_needed, target_mm


def generate_svg(cut_data, output_path, projection_svg=None, title="CUT LIST"):
    all_cuts = []
    # Track final components (glue-ups)
    final_components = []
    for item in cut_data:
        glueup_boards, target_width_mm = _expand_glueups(item)
        qty = item.get("qty", 1)
        stock = item["stock"]
        thickness_mm, stock_width_mm = STOCK_DIMS_MM.get(stock, (19, 100))

        if glueup_boards:
            # Glue-up: qty might already represent individual boards (edge-glue pattern)
            # or qty=1 and we need to expand to glueup_boards (glue-up to X pattern)
            if qty >= glueup_boards:
                # qty already covers the boards (e.g., qty=4, boards=4)
                total_boards_to_cut = qty
                components_count = 1
            else:
                # qty is the number of components, each needing glueup_boards
                # (e.g., qty=2 shelves, each needing 3 boards = 6 total cuts)
                total_boards_to_cut = qty * glueup_boards
                components_count = qty

            for b in range(total_boards_to_cut):
                all_cuts.append({
                    "part": f"{item['part']} (board {b+1}/{total_boards_to_cut})",
                    "base_part": item["part"],
                    "stock": stock,
                    "cut_length": item["cut_length"],
                    "notes": item.get("notes", ""),
                })

            final_components.append({
                "part": item["part"],
                "qty": max(components_count, 1),
                "width_mm": target_width_mm,
                "length_mm": item["cut_length"],
                "thickness_mm": thickness_mm,
                "board_count": glueup_boards,
                "stock": stock,
                "notes": item.get("notes", ""),
            })
        else:
            for i in range(qty):
                label = item["part"] if qty == 1 else f"{item['part']} #{i+1}"
                all_cuts.append({
                    "part": label,
                    "base_part": item["part"],
                    "stock": stock,
                    "cut_length": item["cut_length"],
                    "notes": item.get("notes", ""),
                })

    by_stock = {}
    for cut in all_cuts:
        stock = cut["stock"]
        if stock not in by_stock:
            by_stock[stock] = []
        by_stock[stock].append(cut)

    els = []
    y = MARGIN + 8

    # ── Title block (top) ──
    els.append(
        f'<text x="{MARGIN}" y="{y + 18}" font-family="{FONT_SANS}" font-size="20" '
        f'fill="{C["text"]}" font-weight="700" letter-spacing="-0.5">{_esc(title)}</text>'
    )
    els.append(
        f'<text x="{MARGIN + CONTENT_W}" y="{y + 14}" font-family="{FONT}" font-size="9" '
        f'fill="{C["text_light"]}" text-anchor="end">DIMENSIONS IN INCHES  ·  KERF 1/8"</text>'
    )
    y += 32
    els.append(f'<line x1="{MARGIN}" y1="{y}" x2="{MARGIN + CONTENT_W}" y2="{y}" stroke="{C["border"]}" stroke-width="1.5"/>')
    y += 24

    # ── Projection view (top-right inset) ──
    ASSY_W = 340
    ASSY_H = 280
    assy_x = MARGIN + CONTENT_W - ASSY_W
    assy_y = y
    projection_bottom = y  # track where projection ends
    bom_max_x = MARGIN + CONTENT_W  # default: full width
    has_projection = projection_svg and os.path.exists(projection_svg)
    if has_projection:
        bom_max_x = assy_x - 20  # lumber table stops before drawing area

    # ── Lumber summary ──
    # Group all_cuts by stock size, figure out how many boards to buy
    lumber_summary = {}
    total_parts = 0
    for cut in all_cuts:
        stock = cut["stock"]
        if stock not in lumber_summary:
            lumber_summary[stock] = []
        lumber_summary[stock].append(cut["cut_length"])
    for item in cut_data:
        total_parts += item.get("qty", 1)

    els.append(
        f'<text x="{MARGIN}" y="{y + 12}" font-family="{FONT_SANS}" font-size="11" '
        f'fill="{C["text"]}" font-weight="600" letter-spacing="1">LUMBER</text>'
    )
    y += 24

    bom_w = bom_max_x - MARGIN
    cols = [
        (8,                 int(bom_w * 0.25), "STOCK", "start"),
        (int(bom_w * 0.28), int(bom_w * 0.15), "PIECES", "center"),
        (int(bom_w * 0.45), int(bom_w * 0.25), "BUY", "start"),
        (int(bom_w * 0.72), int(bom_w * 0.28), "NOTES", "start"),
    ]
    els.append(f'<rect x="{MARGIN}" y="{y - 2}" width="{bom_w}" height="22" fill="{C["fill_header"]}"/>')
    for cx, cw, label, align in cols:
        ax = MARGIN + cx + (cw / 2 if align == "center" else (cw if align == "end" else 0))
        els.append(
            f'<text x="{ax}" y="{y + 13}" font-family="{FONT}" font-size="8" '
            f'fill="{C["text_light"]}" text-anchor="{align}" font-weight="600" letter-spacing="0.5">{_esc(label)}</text>'
        )
    y += 24

    for stock_size in sorted(lumber_summary.keys()):
        cut_lengths = lumber_summary[stock_size]
        num_pieces = len(cut_lengths)
        # Figure out boards to buy using the same bin-packing
        cut_tuples = [(f"p{i}", l) for i, l in enumerate(cut_lengths)]
        stock_length_mm, stock_label = pick_stock_length(cut_tuples)
        boards = bin_pack(cut_tuples, stock_length_mm)
        num_boards = len(boards)

        # Check for rip notes
        rip_notes = set()
        for item in cut_data:
            if item["stock"] == stock_size:
                for n in item.get("notes", "").split(";"):
                    n = n.strip()
                    if n.startswith("rip to"):
                        rip_notes.add(n)

        buy_label = f"{num_boards} × {stock_label}"
        notes_str = "; ".join(rip_notes) if rip_notes else ""

        row_vals = [stock_size, str(num_pieces), buy_label, notes_str]
        for (cx, cw, _, align), val in zip(cols, row_vals):
            ax = MARGIN + cx + (cw / 2 if align == "center" else (cw if align == "end" else 0))
            els.append(
                f'<text x="{ax}" y="{y + 12}" font-family="{FONT}" font-size="10" '
                f'fill="{C["text"]}" text-anchor="{align}">{_esc(val)}</text>'
            )
        y += 22
        els.append(f'<line x1="{MARGIN}" y1="{y - 4}" x2="{bom_max_x}" y2="{y - 4}" stroke="{C["fill_header"]}" stroke-width="0.5"/>')

    y += 16
    # Projection box: fixed height, but at least as tall as the lumber table
    if has_projection:
        actual_assy_h = max(ASSY_H, y - assy_y)
        els.append(
            f'<rect x="{assy_x}" y="{assy_y}" width="{ASSY_W}" height="{actual_assy_h}" '
            f'fill="{C["bg"]}" stroke="{C["line"]}" stroke-width="1.5"/>'
        )
        _embed_projection_svg(els, projection_svg, assy_x, assy_y, ASSY_W, actual_assy_h)
        y = max(y, assy_y + actual_assy_h)
    y += 12
    els.append(f'<line x1="{MARGIN}" y1="{y}" x2="{MARGIN + CONTENT_W}" y2="{y}" stroke="{C["line_light"]}" stroke-width="0.5"/>')
    y += 28

    # ── Board Layout diagrams ──
    y = _page_break_if_needed(y, 120)  # need room for header + at least one board
    els.append(
        f'<text x="{MARGIN}" y="{y + 12}" font-family="{FONT_SANS}" font-size="11" '
        f'fill="{C["text"]}" font-weight="600" letter-spacing="1">BOARD LAYOUT</text>'
    )
    y += 28

    BOARD_H = 28
    LABEL_H = 16  # space above each board for part name labels
    total_boards = 0
    total_waste = 0
    total_stock = 0

    for stock_size, cuts in by_stock.items():
        cut_tuples = [(c["part"], c["cut_length"]) for c in cuts]
        stock_length_mm, stock_label = pick_stock_length(cut_tuples)
        boards = bin_pack(cut_tuples, stock_length_mm)

        # Check if stock header + at least first board fits
        y = _page_break_if_needed(y, 20 + BOARD_H + 40)

        board_draw_w = CONTENT_W - 60
        scale = board_draw_w / stock_length_mm

        # Stock label
        els.append(
            f'<text x="{MARGIN}" y="{y + 11}" font-family="{FONT}" font-size="10" '
            f'fill="{C["text"]}" font-weight="600">{_esc(stock_size)}</text>'
        )
        els.append(
            f'<text x="{MARGIN + 40}" y="{y + 11}" font-family="{FONT}" font-size="9" '
            f'fill="{C["text_light"]}">({stock_label} stock, {len(boards)} needed)</text>'
        )
        y += 20

        for b_idx, board_cuts in enumerate(boards):
            y = _page_break_if_needed(y, LABEL_H + BOARD_H + 14)
            bx = MARGIN + 28

            # Board number
            els.append(
                f'<text x="{MARGIN + 12}" y="{y + LABEL_H + BOARD_H / 2 + 4}" font-family="{FONT}" font-size="9" '
                f'fill="{C["text_light"]}" text-anchor="middle">{b_idx + 1}</text>'
            )

            # Part name labels above the board — stagger narrow parts
            by_top = y + LABEL_H
            x = 0
            label_positions = []
            for part_name, length in board_cuts:
                pw = length * scale
                label_positions.append((part_name, bx + x + pw / 2, pw))
                x += pw + KERF * scale

            import re as _re
            # Check if any labels would overlap — estimate text width
            def _clean_label(name):
                name = _re.sub(r'\s*\(board \d+/\d+\)', '', name)
                name = _re.sub(r'\s*\(piece \d+/\d+\)', '', name)
                name = _re.sub(r'\s*#\d+$', '', name)
                return name

            # Detect overlaps: if a label's text width exceeds its part width
            needs_stagger = False
            for i, (pname, lx, pw) in enumerate(label_positions):
                clean = _clean_label(pname)
                text_w = len(clean) * 5.5
                if text_w > pw and pw < 140:
                    needs_stagger = True
                    break

            for i, (pname, lx, pw) in enumerate(label_positions):
                if pw < 15:
                    continue
                clean = _clean_label(pname)
                max_chars = max(3, int(pw / 5.5))
                truncated = clean[:max_chars] + ("…" if len(clean) > max_chars else "")

                stagger = needs_stagger and (i % 2 == 1)
                label_y = y + (2 if stagger else LABEL_H - 3)

                els.append(
                    f'<text x="{lx}" y="{label_y}" font-family="{FONT}" '
                    f'font-size="9" fill="{C["text"]}" text-anchor="middle" font-weight="500">{_esc(truncated)}</text>'
                )
                if stagger:
                    els.append(
                        f'<line x1="{lx}" y1="{label_y + 3}" x2="{lx}" y2="{by_top}" '
                        f'stroke="{C["line_light"]}" stroke-width="0.5"/>'
                    )

            # Board outline
            by = y + LABEL_H
            els.append(
                f'<rect x="{bx}" y="{by}" width="{board_draw_w}" height="{BOARD_H}" '
                f'fill="{C["fill_waste"]}" stroke="{C["line_light"]}" stroke-width="0.75"/>'
            )

            x = 0
            used = 0
            for part_name, length in board_cuts:
                pw = length * scale
                # Part fill
                els.append(
                    f'<rect x="{bx + x}" y="{by}" width="{pw}" height="{BOARD_H}" '
                    f'fill="{C["fill_part"]}" stroke="{C["line"]}" stroke-width="0.75"/>'
                )
                # Cross-hatch
                hx = bx + x
                for hatch_offset in range(0, int(pw + BOARD_H), 8):
                    hx1 = hx + hatch_offset
                    hx2 = hx + hatch_offset - BOARD_H
                    lx1 = max(hx1, hx)
                    ly1 = by + max(0, hx - hx1)
                    lx2 = min(hx2 + BOARD_H, hx + pw)
                    ly2 = min(by + BOARD_H, by + BOARD_H)
                    if lx1 < hx + pw and lx2 > hx:
                        els.append(
                            f'<line x1="{lx1}" y1="{ly1}" x2="{lx2}" y2="{ly2}" '
                            f'stroke="{C["line_light"]}" stroke-width="0.25"/>'
                        )

                # Dimension centered in the part
                if pw > 25:
                    els.append(
                        f'<text x="{bx + x + pw / 2}" y="{by + BOARD_H / 2 + 4}" font-family="{FONT}" '
                        f'font-size="10" fill="{C["text_dim"]}" text-anchor="middle">{_esc(mm_to_in_str(length))}</text>'
                    )

                x += pw
                # Kerf line
                kerf_w = KERF * scale
                if x < board_draw_w - 2:
                    els.append(
                        f'<rect x="{bx + x}" y="{by}" width="{kerf_w}" height="{BOARD_H}" fill="{C["accent"]}" opacity="0.2"/>'
                    )
                    els.append(
                        f'<line x1="{bx + x + kerf_w/2}" y1="{by}" x2="{bx + x + kerf_w/2}" y2="{by + BOARD_H}" '
                        f'stroke="{C["accent"]}" stroke-width="0.5" stroke-dasharray="2,2"/>'
                    )
                x += kerf_w
                used += length + KERF

            waste = stock_length_mm - used + KERF
            total_boards += 1
            total_waste += max(0, waste)
            total_stock += stock_length_mm
            y += LABEL_H + BOARD_H + 14

        # Dimension line under last board for stock length
        _dim_line_h(els, MARGIN + 28, MARGIN + 28 + board_draw_w, y, stock_label)
        y += 28

    y += 12
    els.append(f'<line x1="{MARGIN}" y1="{y}" x2="{MARGIN + CONTENT_W}" y2="{y}" stroke="{C["line_light"]}" stroke-width="0.5"/>')
    y += 28

    # ── Piece detail drawings ──
    # Group cuts by stock + cut length — one card per unique board size
    piece_groups = {}
    for cut in all_cuts:
        key = (cut["stock"], cut["cut_length"])
        if key not in piece_groups:
            piece_groups[key] = {"stock": cut["stock"],
                                 "cut_length": cut["cut_length"],
                                 "count": 0}
        piece_groups[key]["count"] += 1

    PIECE_W = 230
    PIECE_H = 190

    y = _page_break_if_needed(y, PIECE_H + 44)
    els.append(
        f'<text x="{MARGIN}" y="{y + 12}" font-family="{FONT_SANS}" font-size="11" '
        f'fill="{C["text"]}" font-weight="600" letter-spacing="1">PIECE DETAILS</text>'
    )
    els.append(
        f'<text x="{MARGIN + CONTENT_W}" y="{y + 12}" font-family="{FONT}" font-size="8" '
        f'fill="{C["text_light"]}" text-anchor="end">GROUPED BY SIZE</text>'
    )
    y += 32
    GAP_X = 14
    pieces_per_row = max(1, CONTENT_W // (PIECE_W + GAP_X))

    col = 0
    row_y = y

    import re as _re
    for group in piece_groups.values():
        stock = group["stock"]
        cut_len = group["cut_length"]
        piece_qty = group["count"]
        thickness, width = STOCK_DIMS_MM.get(stock, (25, 100))
        thickness_in, width_in = STOCK_DIMS_IN.get(stock, ('1"', '4"'))
        cut_len_in = mm_to_in_str(cut_len)

        cx = MARGIN + col * (PIECE_W + GAP_X)

        # Piece cell border
        els.append(
            f'<rect x="{cx}" y="{row_y}" width="{PIECE_W}" height="{PIECE_H}" '
            f'fill="none" stroke="{C["line_light"]}" stroke-width="0.5"/>'
        )

        # Header
        els.append(
            f'<rect x="{cx}" y="{row_y}" width="{PIECE_W}" height="20" fill="{C["fill_header"]}"/>'
        )
        els.append(
            f'<text x="{cx + 8}" y="{row_y + 14}" font-family="{FONT}" font-size="9" '
            f'fill="{C["text"]}" font-weight="600">{_esc(stock)} x {piece_qty}</text>'
        )

        # Drawing area
        draw_y = row_y + 28
        draw_h = PIECE_H - 70
        draw_w = PIECE_W - 70

        s = min(draw_w / max(cut_len, 1), draw_h / max(width, 1)) * 0.75
        pw = cut_len * s
        ph = width * s

        ox = cx + (PIECE_W - pw) / 2
        oy = draw_y + (draw_h - ph) / 2

        els.append(
            f'<rect x="{ox}" y="{oy}" width="{pw}" height="{ph}" '
            f'fill="{C["fill_part"]}" stroke="{C["line"]}" stroke-width="1"/>'
        )

        cmx, cmy = ox + pw / 2, oy + ph / 2
        cm_size = min(pw, ph) * 0.08
        els.append(f'<line x1="{cmx - cm_size}" y1="{cmy}" x2="{cmx + cm_size}" y2="{cmy}" stroke="{C["line_light"]}" stroke-width="0.5"/>')
        els.append(f'<line x1="{cmx}" y1="{cmy - cm_size}" x2="{cmx}" y2="{cmy + cm_size}" stroke="{C["line_light"]}" stroke-width="0.5"/>')

        _dim_line_h(els, ox, ox + pw, oy + ph + 14, cut_len_in, above=False)
        _dim_line_v(els, ox + pw + 10, oy, oy + ph, width_in)

        els.append(
            f'<text x="{cx + PIECE_W / 2}" y="{row_y + PIECE_H - 6}" font-family="{FONT}" font-size="7.5" '
            f'fill="{C["text_light"]}" text-anchor="middle">t={_esc(thickness_in)}</text>'
        )

        col += 1
        if col >= pieces_per_row:
            col = 0
            row_y += PIECE_H + 12
            row_y = _page_break_if_needed(row_y, PIECE_H + 12)

    if col > 0:
        row_y += PIECE_H + 12
    y = row_y + 12

    # ── Final Components ──
    # Show assembled components: glue-ups at their final dimensions,
    # plus any non-glueup part where qty > 1 (show the finished piece once)
    if final_components:
        els.append(f'<line x1="{MARGIN}" y1="{y}" x2="{MARGIN + CONTENT_W}" y2="{y}" stroke="{C["line_light"]}" stroke-width="0.5"/>')
        y += 28

        COMP_W = 230
        COMP_H = 210

        y = _page_break_if_needed(y, COMP_H + 44)
        els.append(
            f'<text x="{MARGIN}" y="{y + 12}" font-family="{FONT_SANS}" font-size="11" '
            f'fill="{C["text"]}" font-weight="600" letter-spacing="1">COMPOSITES</text>'
        )
        els.append(
            f'<text x="{MARGIN + CONTENT_W}" y="{y + 12}" font-family="{FONT}" font-size="8" '
            f'fill="{C["text_light"]}" text-anchor="end">GLUE-UP ASSEMBLIES</text>'
        )
        y += 32

        # Group identical composites by dimensions
        comp_groups = {}
        for comp in final_components:
            key = (comp["stock"], comp["length_mm"], comp["width_mm"], comp["board_count"])
            if key not in comp_groups:
                comp_groups[key] = {**comp, "total_qty": 0}
            comp_groups[key]["total_qty"] += comp["qty"]

        comp_per_row = max(1, CONTENT_W // (COMP_W + GAP_X))
        col = 0
        row_y = y

        for comp in comp_groups.values():
            cx = MARGIN + col * (COMP_W + GAP_X)
            comp_len = comp["length_mm"]
            comp_width = comp["width_mm"]
            comp_thick = comp["thickness_mm"]
            comp_len_in = mm_to_in_str(comp_len)
            comp_width_in = mm_to_in_str(comp_width)
            comp_thick_in = mm_to_in_str(comp_thick)
            board_count = comp["board_count"]
            total_qty = comp["total_qty"]
            stock_width = STOCK_DIMS_MM.get(comp["stock"], (19, 100))[1]

            # Cell border
            els.append(
                f'<rect x="{cx}" y="{row_y}" width="{COMP_W}" height="{COMP_H}" '
                f'fill="none" stroke="{C["line_light"]}" stroke-width="0.5"/>'
            )

            # Header
            els.append(
                f'<rect x="{cx}" y="{row_y}" width="{COMP_W}" height="20" fill="{C["fill_header"]}"/>'
            )
            els.append(
                f'<text x="{cx + 8}" y="{row_y + 14}" font-family="{FONT}" font-size="9" '
                f'fill="{C["text"]}" font-weight="600">{board_count} boards glued x {total_qty}</text>'
            )

            # Drawing area
            draw_y = row_y + 28
            draw_h = COMP_H - 80
            draw_w = COMP_W - 70

            s = min(draw_w / max(comp_len, 1), draw_h / max(comp_width, 1)) * 0.75
            pw = comp_len * s
            ph = comp_width * s

            ox = cx + (COMP_W - pw) / 2
            oy = draw_y + (draw_h - ph) / 2

            # Draw individual boards within the panel outline
            board_h = stock_width * s
            for b in range(board_count):
                by = oy + b * board_h
                # Clip to panel height
                bh = min(board_h, oy + ph - by)
                if bh <= 0:
                    break
                els.append(
                    f'<rect x="{ox}" y="{by}" width="{pw}" height="{bh}" '
                    f'fill="{C["fill_part"]}" stroke="{C["line_light"]}" stroke-width="0.5"/>'
                )
                # Glue line between boards (dashed)
                if b > 0:
                    els.append(
                        f'<line x1="{ox}" y1="{by}" x2="{ox + pw}" y2="{by}" '
                        f'stroke="{C["accent"]}" stroke-width="0.75" stroke-dasharray="4,3"/>'
                    )

            # Panel outline
            els.append(
                f'<rect x="{ox}" y="{oy}" width="{pw}" height="{ph}" '
                f'fill="none" stroke="{C["line"]}" stroke-width="1.5"/>'
            )

            # Dimensions
            _dim_line_h(els, ox, ox + pw, oy + ph + 14, comp_len_in, above=False)
            _dim_line_v(els, ox + pw + 10, oy, oy + ph, comp_width_in)

            # Footer
            els.append(
                f'<text x="{cx + COMP_W / 2}" y="{row_y + COMP_H - 6}" font-family="{FONT}" font-size="7.5" '
                f'fill="{C["text_light"]}" text-anchor="middle">t={_esc(comp_thick_in)}  ·  {board_count}x {_esc(comp["stock"])}</text>'
            )

            col += 1
            if col >= comp_per_row:
                col = 0
                row_y += COMP_H + 12
                row_y = _page_break_if_needed(row_y, COMP_H + 12)

        if col > 0:
            row_y += COMP_H + 12
        y = row_y + 12

    # ── Summary footer ──
    els.append(f'<line x1="{MARGIN}" y1="{y}" x2="{MARGIN + CONTENT_W}" y2="{y}" stroke="{C["border"]}" stroke-width="1.5"/>')
    y += 16

    efficiency = ((total_stock - total_waste) / total_stock * 100) if total_stock > 0 else 0

    total_cuts = len(all_cuts)
    summary_items = [
        f"PARTS: {total_parts}",
        f"CUTS: {total_cuts}",
        f"BOARDS: {total_boards}",
        f"WASTE: {mm_to_in_str(total_waste)}",
        f"EFFICIENCY: {efficiency:.0f}%",
    ]
    els.append(
        f'<text x="{MARGIN}" y="{y + 12}" font-family="{FONT}" font-size="9" '
        f'fill="{C["text"]}">{"    ·    ".join(summary_items)}</text>'
    )
    y += 32

    # ── Page borders ──
    # Snap total height to page boundary
    import math as _math
    num_pages = _math.ceil((y + MARGIN) / SHEET_H)
    sheet_h = num_pages * SHEET_H
    border_els = []
    _add_page_borders(border_els, sheet_h)

    svg = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{SHEET_W}" height="{sheet_h}" viewBox="0 0 {SHEET_W} {sheet_h}">
  <defs>
    <style>
      @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&amp;family=IBM+Plex+Sans:wght@400;600;700&amp;display=swap');
    </style>
  </defs>
  <rect width="100%" height="100%" fill="{C["bg"]}"/>
  {"".join(border_els)}
  {"".join(els)}
</svg>'''

    with open(output_path, "w") as f:
        f.write(svg)

    return output_path, total_boards, efficiency


def _wrap_text(text, max_chars):
    """Wrap text into lines of max_chars, breaking at spaces."""
    lines = []
    while text:
        if len(text) <= max_chars:
            lines.append(text)
            break
        line = text[:max_chars]
        if ' ' in line:
            line = line[:line.rfind(' ')]
        lines.append(line)
        text = text[len(line):].lstrip()
    return lines


def generate_build_svg(build_instructions, output_path, title="BUILD INSTRUCTIONS", materials=None):
    """
    Generate a separate SVG page for detailed build instructions.
    build_instructions: list of {"phase": str, "steps": [str, ...]}
    Also accepts old format: list of strings (flat steps).
    materials: optional list of {"item": str, "qty": str}
    """
    els = []
    y = MARGIN + 8

    # Title
    els.append(
        f'<text x="{MARGIN}" y="{y + 18}" font-family="{FONT_SANS}" font-size="20" '
        f'fill="{C["text"]}" font-weight="700" letter-spacing="-0.5">{_esc(title)}</text>'
    )
    els.append(
        f'<text x="{MARGIN + CONTENT_W}" y="{y + 14}" font-family="{FONT}" font-size="9" '
        f'fill="{C["text_light"]}" text-anchor="end">BUILD INSTRUCTIONS</text>'
    )
    y += 32
    els.append(f'<line x1="{MARGIN}" y1="{y}" x2="{MARGIN + CONTENT_W}" y2="{y}" stroke="{C["border"]}" stroke-width="1.5"/>')
    y += 28

    # ── Materials table ──
    if materials:
        els.append(
            f'<text x="{MARGIN}" y="{y + 12}" font-family="{FONT_SANS}" font-size="11" '
            f'fill="{C["text"]}" font-weight="600" letter-spacing="1">MATERIALS</text>'
        )
        y += 24
        mat_col_item = MARGIN
        mat_col_qty = MARGIN + int(CONTENT_W * 0.75)
        # Header
        els.append(f'<rect x="{MARGIN}" y="{y - 2}" width="{CONTENT_W}" height="20" fill="{C["fill_header"]}"/>')
        els.append(
            f'<text x="{mat_col_item}" y="{y + 12}" font-family="{FONT}" font-size="8" '
            f'fill="{C["text_light"]}" font-weight="600" letter-spacing="0.5">ITEM</text>')
        els.append(
            f'<text x="{mat_col_qty}" y="{y + 12}" font-family="{FONT}" font-size="8" '
            f'fill="{C["text_light"]}" font-weight="600" letter-spacing="0.5">QTY</text>')
        y += 22
        for mat in materials:
            els.append(
                f'<text x="{mat_col_item}" y="{y + 12}" font-family="{FONT}" font-size="10" '
                f'fill="{C["text"]}">{_esc(mat.get("item", ""))}</text>')
            els.append(
                f'<text x="{mat_col_qty}" y="{y + 12}" font-family="{FONT}" font-size="10" '
                f'fill="{C["text"]}">{_esc(mat.get("qty", ""))}</text>')
            y += 20
            els.append(f'<line x1="{MARGIN}" y1="{y - 4}" x2="{MARGIN + CONTENT_W}" y2="{y - 4}" stroke="{C["fill_header"]}" stroke-width="0.5"/>')
        y += 16
        els.append(f'<line x1="{MARGIN}" y1="{y}" x2="{MARGIN + CONTENT_W}" y2="{y}" stroke="{C["line_light"]}" stroke-width="0.5"/>')
        y += 28

    LINE_H = 15
    STEP_GAP = 6
    PHASE_GAP = 24
    max_chars = int((CONTENT_W - 20) / 5.2)

    # Handle both formats: list of dicts (phased) or list of strings (flat)
    if build_instructions and isinstance(build_instructions[0], str):
        # Flat format — wrap in a single phase
        build_instructions = [{"phase": "", "steps": build_instructions}]

    step_counter = 1
    for phase_data in build_instructions:
        phase_title = phase_data.get("phase", "")
        steps = phase_data.get("steps", [])

        # Phase header — ensure room for header + at least one step
        y = _page_break_if_needed(y, 22 + LINE_H * 2 + STEP_GAP)
        if phase_title:
            els.append(
                f'<text x="{MARGIN}" y="{y + 13}" font-family="{FONT_SANS}" font-size="12" '
                f'fill="{C["text"]}" font-weight="700">{_esc(phase_title)}</text>'
            )
            y += 22

        for step in steps:
            # Check if step fits (estimate wrapped lines)
            est_lines = max(1, len(str(step)) // max_chars + 1)
            y = _page_break_if_needed(y, est_lines * LINE_H + STEP_GAP)

            # Step number
            num_str = f"{step_counter}."
            els.append(
                f'<text x="{MARGIN + 8}" y="{y + 12}" font-family="{FONT}" font-size="10" '
                f'fill="{C["text_light"]}" font-weight="600" text-anchor="end">{num_str}</text>'
            )

            # Step text with wrapping
            step_x = MARGIN + 16
            lines = _wrap_text(str(step), max_chars)
            for line in lines:
                els.append(
                    f'<text x="{step_x}" y="{y + 12}" font-family="{FONT}" font-size="10" '
                    f'fill="{C["text"]}">{_esc(line)}</text>'
                )
                y += LINE_H

            y += STEP_GAP
            step_counter += 1

        y += PHASE_GAP

    # Footer line
    y += 8
    els.append(f'<line x1="{MARGIN}" y1="{y}" x2="{MARGIN + CONTENT_W}" y2="{y}" stroke="{C["border"]}" stroke-width="1.5"/>')
    y += 32

    # Page borders
    import math as _math
    num_pages = _math.ceil((y + MARGIN) / SHEET_H)
    sheet_h = num_pages * SHEET_H
    border_els = []
    _add_page_borders(border_els, sheet_h)

    svg = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="{SHEET_W}" height="{sheet_h}" viewBox="0 0 {SHEET_W} {sheet_h}">
  <defs>
    <style>
      @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&amp;family=IBM+Plex+Sans:wght@400;600;700&amp;display=swap');
    </style>
  </defs>
  <rect width="100%" height="100%" fill="{C["bg"]}"/>
  {"".join(border_els)}
  {"".join(els)}
</svg>'''

    with open(output_path, "w") as f:
        f.write(svg)

    return output_path


def generate_pdf(svg_paths, output_path):
    """Combine multiple SVG pages into a single multi-page PDF.
    Tall SVGs are split into US Letter-sized pages."""
    import cairosvg
    import io
    import copy
    import math

    LETTER_W = 612   # 8.5" in points
    LETTER_H = 792   # 11" in points

    try:
        from pypdf import PdfWriter, PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfWriter, PdfReader
        except ImportError:
            cairosvg.svg2pdf(url=f"file://{svg_paths[0]}", write_to=output_path)
            return output_path

    writer = PdfWriter()
    for svg_path in svg_paths:
        if not os.path.exists(svg_path):
            continue
        pdf_bytes = cairosvg.svg2pdf(url=f"file://{svg_path}")
        reader = PdfReader(io.BytesIO(pdf_bytes))
        for src_page in reader.pages:
            src_h = float(src_page.mediabox.height)

            if src_h <= LETTER_H + 10:
                writer.add_page(src_page)
                continue

            # Split tall page into letter-height slices (top to bottom)
            num_pages = math.ceil(src_h / LETTER_H)
            for i in range(num_pages):
                page = copy.copy(src_page)
                # PDF Y=0 is bottom, content starts at top
                top = src_h - i * LETTER_H
                bottom = max(0, top - LETTER_H)
                page.mediabox.lower_left = (0, bottom)
                page.mediabox.upper_right = (LETTER_W, top)
                writer.add_page(page)

    with open(output_path, "wb") as f:
        writer.write(f)

    return output_path


if __name__ == "__main__":
    # Demo: simple dining table, designed in inches
    def in_to_mm(inches): return round(inches * 25.4)
    demo_cuts = [
        {"part": "Leg", "stock": "4x4", "cut_length": in_to_mm(29), "cut_length_in": '29"', "qty": 4, "notes": "chamfer bottom"},
        {"part": "Top_Plank", "stock": "2x8", "cut_length": in_to_mm(60), "cut_length_in": '60"', "qty": 4, "notes": 'edge-glue for 29" wide top'},
        {"part": "Long_Apron", "stock": "1x6", "cut_length": in_to_mm(48), "cut_length_in": '48"', "qty": 2, "notes": "mortise & tenon"},
        {"part": "Short_Apron", "stock": "1x6", "cut_length": in_to_mm(22), "cut_length_in": '22"', "qty": 2, "notes": "mortise & tenon"},
        {"part": "Stretcher", "stock": "2x4", "cut_length": in_to_mm(44), "cut_length_in": '44"', "qty": 1, "notes": ""},
    ]
    # Assembly: simple dining table
    # pos = [x, z, y] — x=left-right, z=front-back, y=up
    # size = [w, d, h] — w=left-right, d=front-back, h=up
    # Wireframe edges: [x1, y1, z1, x2, y2, z2]
    # x = left-right (width), y = up, z = front-back (depth)
    W, D, H = 60, 29, 30  # overall
    TH = 1.5  # top thickness
    LI = 3    # leg inset from edge
    AH = 5.5  # apron height
    AY = H - TH - AH  # apron bottom y

    def box_edges(x, y, z, w, h, d):
        """Return 12 edges of a box."""
        corners = [
            (x, y, z), (x+w, y, z), (x+w, y, z+d), (x, y, z+d),       # bottom
            (x, y+h, z), (x+w, y+h, z), (x+w, y+h, z+d), (x, y+h, z+d),  # top
        ]
        edges = []
        # Bottom rect
        for i in range(4): edges.append(corners[i] + corners[(i+1)%4])
        # Top rect
        for i in range(4): edges.append(corners[4+i] + corners[4+(i+1)%4])
        # Verticals
        for i in range(4): edges.append(corners[i] + corners[i+4])
        return edges

    all_edges = []
    # Tabletop
    all_edges += box_edges(0, H - TH, 0, W, TH, D)
    # 4 legs
    for lx, lz in [(LI, LI), (W - LI - 3.5, LI), (LI, D - LI - 3.5), (W - LI - 3.5, D - LI - 3.5)]:
        all_edges += box_edges(lx, 0, lz, 3.5, H - TH, 3.5)
    # Front apron
    all_edges += box_edges(LI + 3.5, AY, LI, W - 2*LI - 7, AH, 0.75)
    # Back apron
    all_edges += box_edges(LI + 3.5, AY, D - LI - 0.75, W - 2*LI - 7, AH, 0.75)
    # Left apron
    all_edges += box_edges(LI, AY, LI + 3.5, 0.75, AH, D - 2*LI - 7)
    # Right apron
    all_edges += box_edges(W - LI - 0.75, AY, LI + 3.5, 0.75, AH, D - 2*LI - 7)
    # Stretcher
    all_edges += box_edges(LI + 3.5, 8, D/2 - 0.75, W - 2*LI - 7, 3.5, 1.5)

    # Use the real FreeCAD projection if available
    proj_svg = os.path.join(os.path.dirname(__file__), "output", "table_projection.svg")

    out = os.path.join(os.path.dirname(__file__), "output", "demo_cutsheet.svg")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    proj = proj_svg if os.path.exists(proj_svg) else None
    path, boards, eff = generate_svg(demo_cuts, out, projection_svg=proj, title="Dining Table")

    # Generate build instructions as separate page
    demo_build = [
        {
            "phase": "1. MILLING & DIMENSIONING",
            "steps": [
                'Joint one face and one edge of each board on the jointer. Mark the jointed face with a pencil cabinetmaker\'s triangle.',
                'Plane all 4x4 leg stock to 3-1/2" square. Check with a combination square on all four faces — must be 90\u00b0 within 1/64".',
                'Rip the 2x8 top planks to 7" on the table saw to remove the factory rounded edge. Save offcuts for test joints later.',
                'Crosscut all parts to final length per the cut list. Use a stop block clamped to your miter saw fence for repeated cuts (all 4 legs identical, both long aprons identical, etc.).',
                'Label every part with painter\'s tape — mark orientation (top/bottom, inside/outside) now while you can still tell them apart.',
            ]
        },
        {
            "phase": "2. TABLETOP GLUE-UP",
            "steps": [
                'Arrange the 4 top planks for the best grain match. Alternate growth ring direction (cup up, cup down) to minimize seasonal warping.',
                'Joint the mating edges of each plank. Test by holding two boards together against a light source — no light should pass through the joint.',
                'Dry-clamp the full top assembly. Mark alignment triangles across the joints so you can realign quickly during glue-up.',
                'Glue up in pairs first: glue two 2-board panels, clamp with pipe clamps every 12", alternating above and below to prevent cupping. Use cauls to keep faces flush. Titebond III open time is ~10 min — work quickly.',
                'After 2 hours, scrape off squeezed-out glue with a chisel (semi-cured glue scrapes cleaner than wet or dry). Let cure overnight.',
                'Glue the two 2-board panels together the next day using the same technique.',
                'After full cure (24 hrs), joint one edge on the jointer, then rip to 29" on the table saw. Crosscut to 60" with a circular saw and straightedge guide.',
                'Flatten the top with a #5 jack plane or belt sander (80 grit), working diagonally. Finish with a random orbit sander: 80 → 120 → 150 grit. Leave 220 for final sanding after assembly.',
            ]
        },
        {
            "phase": "3. MORTISE & TENON JOINERY",
            "steps": [
                'Mark mortise locations on legs. Each leg receives two mortises on adjacent inside faces. Mortise centers are 4" down from the top of the leg. Mortises are 3/8" wide x 3" long x 1" deep.',
                'Cut mortises with a plunge router and 3/8" upcut spiral bit. Use a self-centering mortising jig or clamp a straightedge guide. Rout in 1/4" depth increments. Square the corners with a sharp 3/8" chisel.',
                'Cut tenons on all 4 long apron ends and all 4 short apron ends. Use a dado stack on the table saw with the apron standing on end against the miter gauge. Tenon dimensions: 1" long, 3/8" thick, 3" wide. Leave 1/16" gap at the bottom of the mortise for excess glue.',
                'TEST EVERY JOINT. Each tenon should slide into its specific mortise with firm hand pressure — no hammering, no slop. Label mating parts (e.g., "A1" on the tenon and inside the mortise). Shave with a shoulder plane if too tight.',
                'Cut the stretcher tenons using the same dado stack setup. Stretcher tenons: 1" long, 3/8" thick, 2" wide, centered on the 3-1/2" face.',
                'Cut corresponding stretcher mortises in the center of the two long aprons\' inside faces, 8" up from the bottom edge.',
            ]
        },
        {
            "phase": "4. DRY FIT & ASSEMBLY",
            "steps": [
                'Dry-fit the entire base: 4 legs + 2 long aprons + 2 short aprons + stretcher. Check for square by measuring diagonals — they must match within 1/16".',
                'Mark all joints for orientation. Disassemble.',
                'Glue up in sub-assemblies. FIRST: glue two short-side frames (2 legs + 1 short apron each). Apply glue to both mortise walls and tenon cheeks. Clamp with bar clamps across the apron. Check for square. Let cure overnight.',
                'NEXT DAY: connect the two short-side frames with the two long aprons and stretcher. This is a complex glue-up — have all clamps pre-set, glue bottle open, and a helper if possible. Apply glue to all joints, assemble, clamp, and immediately check diagonals for square. Adjust clamp pressure to pull into square if needed.',
                'Let the base cure 24 hours before any stress.',
            ]
        },
        {
            "phase": "5. TOP ATTACHMENT",
            "steps": [
                'Position the top upside-down on a padded surface. Center the base (also upside-down) on the top. The top should overhang evenly on all sides.',
                'Attach using figure-8 tabletop fasteners (preferred) or elongated pocket-screw holes. The top MUST be free to expand and contract across its width with seasonal humidity changes — never glue the top to the base.',
                'Install 2 figure-8 fasteners per long apron and 1 per short apron. Rout a shallow mortise for each fastener so it sits flush.',
            ]
        },
        {
            "phase": "6. EDGE TREATMENTS",
            "steps": [
                'Chamfer the bottom of each leg: 3/16" x 45\u00b0, using a block plane or router with a chamfer bit. This prevents splintering when the table is slid across the floor.',
                'Round over all exposed top edges with a 1/8" roundover bit in a trim router, or hand-sand to a soft ease. Route the end grain first, then the long grain (this cleans up any tearout at the corners).',
            ]
        },
        {
            "phase": "7. FINISHING",
            "steps": [
                'Final sand the entire piece to 220 grit with a random orbit sander. Hand-sand any profiles or inside corners.',
                'Raise the grain: wipe with a damp cloth, let dry 30 minutes, then sand lightly with 220 to knock off the raised fibers. This prevents the first coat of finish from feeling rough.',
                'Apply finish of choice. For a dining table, recommended options: (a) Arm-R-Seal oil/poly blend — 3 coats, sand with 320 between coats; (b) water-based polyurethane — 3-4 coats; (c) hardwax oil (Rubio Monocoat or Osmo) — 1-2 coats, easiest to repair.',
                'Let final coat cure per manufacturer instructions (typically 24-72 hours) before use. Place felt pads under the legs.',
            ]
        },
    ]

    build_path = os.path.join(os.path.dirname(__file__), "output", "demo_build.svg")
    generate_build_svg(demo_build, build_path, title="Dining Table")

    # Combine into multi-page PDF
    pdf_path = os.path.join(os.path.dirname(__file__), "output", "demo_dining_table.pdf")
    generate_pdf([out, build_path], pdf_path)

    print(f"Generated: {path} ({boards} boards, {eff:.0f}% efficiency)")
    print(f"PDF: {pdf_path}")
