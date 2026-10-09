"""Generate a fixture config JSON from a fixture input folder.

Input folder:
  FX ID<n>.txt          fixture ID in the file name (else --fixture-id)
  Nutzen_CNT.jpg        contour image, source of the resource image
  MCU_Pos[_ADJUSTED].pdf  PCB drawing, MCUs filled in color, optional legend "PROG1A / 1B1 <type>"
                        or "P1A <type>" (else --pdf <other PDF>)
  Nutzen.pdf            panel drawing, only used with --panel (several equal boards, labels
                        PCB<n> and PROG<id><A|B><slot> per board in Nutzen_CNT.jpg)

Output:
  <out-dir>/<Name>.json            fixture config
  <out-dir>/resources/<Name>.jpg   resource image (only created if missing: CNT image, 40 px white margin, frame)
  preview/<Name>.png               overlay of all shapes on the image, for a visual check

How it works:
  - ShapeModel = black frame of the resource image, or the outer contour of the drawing if it has no frame.
  - The board outline path and the colored MCU boxes are read as vectors from the PDF.
  - Scale and offset PDF -> image are fitted by matching the PDF board outline to the image contour.
  - NestShape = PDF board outline in image pixels. MCUs = colored boxes, split horizontally when one
    box has several programmers ("PROG1A / 1B1"), a point instead of a rectangle for --point-name.
  - --panel: one nest per board of Nutzen.pdf (NestShape, NestShape_180 for boards turned by 180 degrees).
    The board is located in the MCU PDF (also mirrored or rotated) to carry the MCU box into every nest.
    nestId and programmer come from the PCB<n> / PROG labels read by OCR inside each board.
  - Polygon coordinates are relative to the image centre. A placement (shapeXCoord/YCoord) is the
    image position of the shape's first coordinate.

Usage:
  python fixture_gen.py "<data>/input/<NN>_<Folder>" --name <Name>
  python fixture_gen.py "<input folder>" --name <Name> --shape-name "IO driver=UART" --out-dir <temp dir>
  python fixture_gen.py "<input folder>" --name <Name> --fixture-id <n> --pdf <other PDF> --out-dir <output dir>
  python fixture_gen.py "<input folder>" --name <Name> --panel --out-dir <output dir>
"""
import argparse
import colorsys
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
import pymupdf

DARK_THRESHOLD = 160
MCU_GRID = 10
BORDER_THICKNESS = 3
FRAME_MARGIN = 40
FRAME_MIN_COVERAGE = 0.98
STRIKE_COLOR_MIN_RED = 0.8
PROGRAMMER_TOKEN = re.compile(r"^(?:PROG|P)?(\d+)([AB])(\d?)$", re.IGNORECASE)
REFERENCE_DESIGNATOR = re.compile(r"^[A-Z]{1,3}\d+$")
PCB_LABEL = re.compile(r"^PCB(\d+)$", re.IGNORECASE)
PDF_RENDER_DPI = 200
OCR_SCALE = 0.5
CHIP_OCR_DPIS = (100, 200, 300)
MIN_BOARD_MATCH = 0.9


# ---------- input folder ----------

def fixture_id(folder, given):
    if given is not None:
        return given
    for path in folder.glob("FX ID*.txt"):
        if re.search(r"FX ID\s*\d+\s*-\s*\d+", path.name):
            raise SystemExit(f"'{path.name}' is an ID range. Ask which ID to use and pass it with --fixture-id.")
        match = re.search(r"FX ID\s*(\d+)", path.name)
        if match:
            return int(match.group(1))
    raise SystemExit(f"No 'FX ID<n>.txt' in {folder}. Pass the fixture ID with --fixture-id.")


def mcu_pdf(folder, given):
    if given is not None:
        path = given if given.is_absolute() else folder / given
        if not path.exists():
            raise SystemExit(f"PDF not found: {path}")
        return path
    adjusted = sorted(folder.glob("MCU_Pos*ADJUSTED*.pdf"))
    if adjusted:
        return adjusted[0]
    plain = folder / "MCU_Pos.pdf"
    if plain.exists():
        return plain
    others = ", ".join(p.name for p in sorted(folder.glob("*.pdf"))) or "none"
    raise SystemExit(f"No MCU_Pos.pdf in {folder} (other PDFs: {others}). Pass one with --pdf.")


# ---------- resource image ----------

def resource_image(folder, out_dir, name, scale):
    resources = out_dir / "resources"
    for ext in (".jpg", ".png"):
        existing = resources / (name + ext)
        if existing.exists():
            return existing, False
    image = cv2.imread(str(folder / "Nutzen_CNT.jpg"), cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit(f"No Nutzen_CNT.jpg in {folder}")
    if scale != 1.0:
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    # White margin first, so the frame never covers a board edge that touches the CNT image border.
    image = cv2.copyMakeBorder(image, FRAME_MARGIN, FRAME_MARGIN, FRAME_MARGIN, FRAME_MARGIN,
                               cv2.BORDER_CONSTANT, value=(255, 255, 255))
    h, w = image.shape[:2]
    cv2.rectangle(image, (12, 12), (w - 13, h - 13), (0, 0, 0), 5)
    resources.mkdir(parents=True, exist_ok=True)
    target = resources / (name + ".jpg")
    cv2.imwrite(str(target), image, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return target, True


def read_gray(path):
    data = np.fromfile(str(path), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)


def dark_run_centres(line):
    dark = np.concatenate(([False], line < DARK_THRESHOLD, [False]))
    edges = np.flatnonzero(np.diff(dark.astype(np.int8)))
    return [(start + end - 1) / 2 for start, end in zip(edges[::2], edges[1::2])]


def detect_frame(gray):
    """Centre line of the outer black frame (left, top, right, bottom), or None if there is no frame.

    A frame counts only if all four sides are continuous dark lines between the corners.
    Without this check the outermost lines of the drawing itself would be taken as the frame."""
    h, w = gray.shape
    row = dark_run_centres(gray[h // 2, :])
    col = dark_run_centres(gray[:, w // 2])
    if len(row) < 2 or len(col) < 2:
        return None
    left, top, right, bottom = round(row[0]), round(col[0]), round(row[-1]), round(col[-1])
    dark = gray < DARK_THRESHOLD

    def covered(band):
        return band.any(axis=0 if band.shape[0] <= band.shape[1] else 1).mean() >= FRAME_MIN_COVERAGE

    sides = (dark[top - 3:top + 4, left:right + 1], dark[bottom - 3:bottom + 4, left:right + 1],
             dark[top:bottom + 1, left - 3:left + 4], dark[top:bottom + 1, right - 3:right + 4])
    if min(left, top) < 3 or right + 4 > w or bottom + 4 > h or not all(covered(s) for s in sides):
        return None
    return left, top, right, bottom


def frame_polygon(gray, lines, warnings):
    """ShapeModel polygon: the black frame, or the outer contour of the drawing if there is no frame."""
    frame = detect_frame(gray)
    if frame is not None:
        left, top, right, bottom = frame
        return [(left, top), (left, bottom), (right, bottom), (right, top), (left, top)]
    warnings.append("No frame in the resource image, ShapeModel created from the outer contour of the drawing")
    mask = cv2.morphologyEx(lines, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        raise SystemExit("Resource image is empty: no frame and no drawing found")
    contour = max(contours, key=cv2.contourArea)
    return start_down_left_side(cv2.approxPolyDP(contour, 2.0, True)[:, 0, :].tolist())


def bounding_box(poly):
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


# ---------- PDF vectors ----------

def flatten(item, step=0.5):
    """Points along one drawing item, in PDF units."""
    kind = item[0]
    if kind == "l":
        p, q = np.array(item[1]), np.array(item[2])
        n = max(2, int(np.hypot(*(q - p)) / step) + 1)
        return [p + (q - p) * t for t in np.linspace(0, 1, n)]
    if kind == "c":
        p0, p1, p2, p3 = (np.array(pt) for pt in item[1:5])
        n = max(4, int((np.hypot(*(p1 - p0)) + np.hypot(*(p2 - p1)) + np.hypot(*(p3 - p2))) / step) + 1)
        return [(1 - t) ** 3 * p0 + 3 * (1 - t) ** 2 * t * p1 + 3 * (1 - t) * t ** 2 * p2 + t ** 3 * p3
                for t in np.linspace(0, 1, n)]
    if kind == "re":
        r = item[1]
        corners = [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1), (r.x0, r.y0)]
        return [pt for a, b in zip(corners, corners[1:]) for pt in flatten(("l", a, b), step)]
    if kind == "qu":
        q = item[1]
        corners = [q.ul, q.ur, q.lr, q.ll, q.ul]
        return [pt for a, b in zip(corners, corners[1:]) for pt in flatten(("l", a, b), step)]
    return []


def segments(drawing):
    return [np.array(flatten(item)) for item in drawing["items"] if flatten(item)]


def is_gray(color):
    return color is None or (abs(color[0] - color[1]) < 0.02 and abs(color[1] - color[2]) < 0.02)


def board_drawing(drawings, page):
    """Largest stroked black path that is not a thin line of the drawing frame or title block."""
    min_w, min_h = page.rect.width * 0.1, page.rect.height * 0.1
    candidates = [d for d in drawings
                  if d.get("color") is not None and is_gray(d["color"]) and d["color"][0] < 0.2
                  and d["rect"].width > min_w and d["rect"].height > min_h]
    if not candidates:
        raise SystemExit("Board outline not found in PDF")
    return max(candidates, key=lambda d: d["rect"].width * d["rect"].height)


def colored_boxes(drawings):
    return [(tuple(round(c, 3) for c in d["fill"]), d["rect"])
            for d in drawings if d.get("fill") is not None and not is_gray(d["fill"])]


def struck_words(words, drawings):
    """Words crossed out by a red horizontal line."""
    strikes = [d["rect"] for d in drawings
               if d.get("color") is not None and d["color"][0] > STRIKE_COLOR_MIN_RED
               and max(d["color"][1], d["color"][2]) < 0.5 and d["rect"].height < 3]
    result = set()
    for i, w in enumerate(words):
        x0, y0, x1, y1 = w[:4]
        for s in strikes:
            overlap = min(x1, s.x1) - max(x0, s.x0)
            if overlap > 0.5 * (x1 - x0) and y0 <= (s.y0 + s.y1) / 2 <= y1:
                result.add(i)
    return result


def legend_text(box, words, struck):
    line = [w for i, w in enumerate(words) if i not in struck
            and box.x1 < w[0] < box.x1 + 250 and box.y0 <= (w[1] + w[3]) / 2 <= box.y1]
    return " ".join(w[4] for w in sorted(line, key=lambda w: w[0]))


def parse_legend(text):
    """'PROG1A / 1B1 ABC1' -> ([(1, 0, 0), (1, 1, 1)], 'ABC1')."""
    programmers, name = [], []
    for token in text.replace("/", " ").split():
        match = PROGRAMMER_TOKEN.match(token)
        if match:
            programmers.append((int(match.group(1)), 0 if match.group(2).upper() == "A" else 1,
                                int(match.group(3) or 0)))
        else:
            name.append(token)
    return programmers, " ".join(name)


def reference_inside(box, words):
    refs = [w[4] for w in words if REFERENCE_DESIGNATOR.match(w[4])
            and box.x0 <= (w[0] + w[2]) / 2 <= box.x1 and box.y0 <= (w[1] + w[3]) / 2 <= box.y1]
    return refs[0] if refs else None


def read_pdf(path):
    page = pymupdf.open(str(path))[0]
    drawings = page.get_drawings()
    words = page.get_text("words")
    board = board_drawing(drawings, page)
    struck = struck_words(words, drawings)
    legends, chips = {}, []
    for color, rect in colored_boxes(drawings):
        if board["rect"].contains(rect):
            chips.append((color, rect))
        else:
            legends[color] = legend_text(rect, words, struck)
    notes = match_legends_by_hue(chips, legends)
    return board, chips, legends, words, notes


def hue_distance(a, b):
    """Distance on the hue circle, 0..0.5."""
    d = abs(colorsys.rgb_to_hsv(*a)[0] - colorsys.rgb_to_hsv(*b)[0])
    return min(d, 1 - d)


def match_legends_by_hue(chips, legends):
    """Give each chip without an exactly matching legend color the unused legend with the nearest hue.

    The legend is re-keyed to the chip color. Seen: chip turquoise (0.25, 0.88, 0.82), legend light cyan
    (0.75, 1, 1). Returns notes for the user."""
    notes = []
    for color, _ in chips:
        unused = [c for c in legends if c not in {chip for chip, _ in chips} and legends[c]]
        if color in legends or not unused:
            continue
        nearest = min(unused, key=lambda c: hue_distance(c, color))
        legends[color] = legends.pop(nearest)
        notes.append(f"MCU color {color} has no exact legend, matched by hue to '{legends[color]}' {nearest}")
    return notes


# ---------- PDF -> image transform ----------

def outline_points(board):
    points = np.concatenate(segments(board))
    return points - points.min(axis=0)


def coarse_fit(lines, points, frame):
    """Best scale and offset on a downscaled image by template matching."""
    k = 1200.0 / lines.shape[1]
    small = cv2.resize(lines.astype(np.float32), None, fx=k, fy=k, interpolation=cv2.INTER_AREA) > 0.05
    small = cv2.dilate(small.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(np.float32)
    size = points.max(axis=0)
    s_max = min((frame[2] - frame[0]) / size[0], (frame[3] - frame[1]) / size[1])
    best = (-1.0, None, None)
    for s in np.linspace(0.4 * s_max, s_max, 150):
        pts = np.round(points * s * k).astype(int)
        tmpl = np.zeros((pts[:, 1].max() + 1, pts[:, 0].max() + 1), np.float32)
        tmpl[pts[:, 1], pts[:, 0]] = 1
        if tmpl.shape[0] > small.shape[0] or tmpl.shape[1] > small.shape[1]:
            continue
        result = cv2.matchTemplate(small, tmpl, cv2.TM_CCORR) / tmpl.sum()
        _, score, _, loc = cv2.minMaxLoc(result)
        if score >= best[0] - 1e-6:
            best = (score, s, (loc[0] / k, loc[1] / k))
    return best[1], np.array(best[2])


def refine_fit(lines, points, scale, offset):
    """Pull the outline onto the centre of the image lines.

    Cost = distance to the nearest line minus a blurred line intensity, so that the
    outline is drawn into the lines from afar and centred within their thickness."""
    dist = cv2.distanceTransform((1 - lines).astype(np.uint8), cv2.DIST_L2, 3)
    ridge = cv2.GaussianBlur(lines.astype(np.float32), (0, 0), 3)
    h, w = dist.shape
    sample = points[:: max(1, len(points) // 4000)]

    def pixels(s, d):
        p = np.round(sample * s + d).astype(int)
        p[:, 0] = p[:, 0].clip(0, w - 1)
        p[:, 1] = p[:, 1].clip(0, h - 1)
        return p[:, 1], p[:, 0]

    def cost(s, d):
        idx = pixels(s, d)
        return float(dist[idx].mean() - ridge[idx].mean())

    s, d = scale, offset.astype(float)
    ds, dd = s * 0.01, 4.0
    current = cost(s, d)
    while ds > s * 1e-5 or dd > 0.25:
        improved = False
        for cand_s, cand_d in ((s + ds, d), (s - ds, d),
                               (s, d + (dd, 0)), (s, d - (dd, 0)), (s, d + (0, dd)), (s, d - (0, dd))):
            c = cost(cand_s, cand_d)
            if c < current - 1e-9:
                s, d, current, improved = cand_s, np.array(cand_d, float), c, True
        if not improved:
            ds, dd = ds / 2, dd / 2
    return s, d, float(dist[pixels(s, d)].mean())


# ---------- shapes ----------

def nest_polygon(board, origin_pdf, scale, offset, image_shape):
    mask = np.zeros(image_shape, np.uint8)
    for seg in segments(board):
        pts = np.round((seg - origin_pdf) * scale + offset).astype(np.int32)
        cv2.polylines(mask, [pts], False, 255, 1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    contour = max(contours, key=cv2.contourArea)
    poly = cv2.approxPolyDP(contour, 2.0, True)[:, 0, :].tolist()
    return start_down_left_side(poly)


def start_down_left_side(poly):
    """Start at the top of the left edge and run down the left side, like the hand-made configs."""
    min_x = min(p[0] for p in poly)
    start = min((i for i, p in enumerate(poly) if p[0] <= min_x + 2), key=lambda i: poly[i][1])
    poly = poly[start:] + poly[:start]
    if poly[1][1] < poly[0][1]:
        poly = [poly[0]] + poly[1:][::-1]
    return poly + [poly[0]]


def snap(value):
    return int(round(value / MCU_GRID) * MCU_GRID)


def shape_entry(shape_id, coords):
    return {"shapeId": shape_id, "borderThickness": BORDER_THICKNESS,
            "coordinates": [{"x": int(x), "y": int(y)} for x, y in coords],
            "backgroundColor": None, "shapeXCoordinate": 0.0, "shapeYCoordinate": 0.0}


def placement(shape_id, x, y):
    return {"shapeId": shape_id, "shapeXCoord": int(x), "shapeYCoord": int(y)}


def build_mcus(chips, legends, words, to_image, origin, args, warnings):
    shapes, mcus, next_programmer = {}, [], 1
    for color, rect in sorted(chips, key=lambda c: (c[1].x0, c[1].y0)):
        programmers, name = parse_legend(legends.get(color, ""))
        if not name:
            name = reference_inside(rect, words) or f"{len(mcus) + 1}"
        if not programmers:
            programmers = [(next_programmer, 0, 0)]
            warnings.append(f"No legend for MCU '{name}', assumed PROG{next_programmer}A")
        next_programmer = max(next_programmer, max(p[0] for p in programmers) + 1)
        shape_id = args.shape_names.get(name, "MCU_" + name.replace(" ", "_"))
        (x0, y0), (x1, y1) = to_image((rect.x0, rect.y0)), to_image((rect.x1, rect.y1))
        if name in args.point_names:
            cx, cy = round((x0 + x1) / 2), round((y0 + y1) / 2)
            shapes.setdefault(shape_id, [(cx - origin[0], cy - origin[1]), (cx - origin[0], cy - origin[1] + 5),
                                         (cx - origin[0], cy - origin[1])])
            for prog in programmers:
                mcus.append((prog, placement(shape_id, cx, cy)))
            continue
        part_w = snap((x1 - x0) / len(programmers))
        height = snap(y1 - y0)
        rect_coords = [(0, 0), (part_w, 0), (part_w, height), (0, height), (0, 0)]
        if shapes.setdefault(shape_id, rect_coords) != rect_coords:
            shape_id = f"{shape_id}_{len(shapes)}"
            shapes[shape_id] = rect_coords
        for i, prog in enumerate(programmers):
            mcus.append((prog, placement(shape_id, snap(x0) + i * part_w, snap(y0))))
    entries = [{"mcuId": i + 1, "shape": place,
                "programmer": {"programmerId": p[0], "channel": p[1], "slot": p[2]}}
               for i, (p, place) in enumerate(sorted(mcus, key=lambda m: m[0]))]
    return shapes, entries


def build_single(pdf_path, gray, lines, frame_poly, origin, args, warnings):
    """One nest from the board outline of the MCU PDF, MCUs and programmers from its colored boxes and legend."""
    board, chips, legends, words, notes = read_pdf(pdf_path)
    warnings.extend(notes)
    points = outline_points(board)
    origin_pdf = np.concatenate(segments(board)).min(axis=0)
    scale, offset = coarse_fit(lines, points, bounding_box(frame_poly))
    scale, offset, error = refine_fit(lines, points, scale, offset)

    def to_image(pt):
        return tuple((np.array(pt) - origin_pdf) * scale + offset)

    if error > 3.0:
        warnings.append(f"Board outline fits the image poorly (mean distance {error:.1f} px), check the preview")
    nest = nest_polygon(board, origin_pdf, scale, offset, gray.shape)
    mcu_shapes, mcus = build_mcus(chips, legends, words, to_image, origin, args, warnings)
    shapes = [("NestShape", [(x - origin[0], y - origin[1]) for x, y in nest])] + list(mcu_shapes.items())
    nests = [{"nestId": 1, "shape": placement("NestShape", *nest[0]), "mcus": mcus}]
    report = [f"Fit:      scale {scale:.4f} px/pt, offset ({offset[0]:.1f}, {offset[1]:.1f}), mean error {error:.2f} px",
              f"Nest:     {len(nest)} points, MCUs: {len(mcus)}"]
    for mcu in mcus:
        p = mcu["programmer"]
        report.append(f"  MCU {mcu['mcuId']}: {mcu['shape']['shapeId']} at ({mcu['shape']['shapeXCoord']}, "
                      f"{mcu['shape']['shapeYCoord']}) programmer {p['programmerId']}/{p['channel']}/{p['slot']}")
    return shapes, nests, report


# ---------- panel: several equal boards ----------

def panel_drawings(path):
    """Board outlines of a panel PDF (the largest group of equal stroked paths) and the panel outline."""
    page = pymupdf.open(str(path))[0]
    stroked = [d for d in page.get_drawings() if d.get("color") is not None]
    groups = defaultdict(list)
    for d in stroked:
        groups[(len(d["items"]), round(d["rect"].width, 1), round(d["rect"].height, 1))].append(d)
    repeated = [g for g in groups.values() if len(g) >= 2]
    if not repeated:
        raise SystemExit(f"No repeated board outline in {path.name}")
    boards = max(repeated, key=lambda g: g[0]["rect"].width * g[0]["rect"].height)
    outline = max(stroked, key=lambda d: d["rect"].width * d["rect"].height)
    return boards, outline


def centroid_offset(board):
    r = board["rect"]
    return np.concatenate(segments(board)).mean(axis=0) - ((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)


def render_gray(page):
    pix = page.get_pixmap(dpi=PDF_RENDER_DPI, colorspace=pymupdf.csGRAY)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)[:, :pix.width]


def flip(points, size, factors):
    return np.stack([points[:, i] if f > 0 else size[i] - points[:, i] for i, f in enumerate(factors)], axis=1)


def locate_board(page, board):
    """Find the panel board in the single-board MCU PDF, which may show it mirrored or rotated.

    Returns PDF point of the MCU PDF -> board point (panel PDF units from the board's top left),
    the variant name and the share of outline points that lie on lines."""
    gray = render_gray(page)
    lines = (gray < DARK_THRESHOLD).astype(np.uint8)
    near_line = cv2.dilate(lines, np.ones((3, 3), np.uint8))
    points = outline_points(board)
    size = points.max(axis=0)
    h, w = lines.shape
    best = None
    for name, factors in (("as drawn", (1, 1)), ("mirrored left-right", (-1, 1)),
                          ("mirrored top-bottom", (1, -1)), ("rotated 180", (-1, -1))):
        variant = flip(points, size, factors)
        scale, offset = coarse_fit(lines, variant, (0, 0, w - 1, h - 1))
        scale, offset, _ = refine_fit(lines, variant, scale, offset)
        p = np.round(variant * scale + offset).astype(int)
        score = near_line[p[:, 1].clip(0, h - 1), p[:, 0].clip(0, w - 1)].mean()
        if best is None or score > best[0]:
            best = (score, name, factors, scale, offset)
    score, name, factors, scale, offset = best
    k = PDF_RENDER_DPI / 72.0

    def to_board(pt):
        return flip((np.array([pt], float) * k - offset) / scale, size, factors)[0]

    return to_board, name, float(score)


def ocr_words(image, scale=OCR_SCALE):
    """(text without spaces, centre, confidence) of each text line found by OCR, in image pixels."""
    from rapidocr_onnxruntime import RapidOCR
    small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale != 1 else image
    result, _ = RapidOCR()(cv2.cvtColor(small, cv2.COLOR_GRAY2BGR))
    return [(text.replace(" ", ""), np.mean(box, axis=0) / scale, conf) for box, text, conf in result or []]


def chip_reference(page, rect, words):
    """Reference designator inside a chip box, from the PDF text or by OCR if the text is drawn as strokes."""
    ref = reference_inside(rect, words)
    if ref:
        return ref
    # OCR reads small stroked text differently per resolution (seen: "M0D1"), keep the most confident match.
    clip = pymupdf.Rect(rect.x0 - 2, rect.y0 - 2, rect.x1 + 2, rect.y1 + 2)
    refs = []
    for dpi in CHIP_OCR_DPIS:
        pix = page.get_pixmap(dpi=dpi, clip=clip, colorspace=pymupdf.csGRAY)
        image = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)[:, :pix.width]
        refs += [(conf, text) for text, _, conf in ocr_words(image, 1) if REFERENCE_DESIGNATOR.match(text)]
    return max(refs)[1] if refs else None


def build_panel(folder, pdf_path, gray, lines, frame_bbox, origin, args, warnings):
    """One nest per board of Nutzen.pdf, nest IDs and programmers from the PCB<n> / PROG labels of the image."""
    panel_pdf = folder / "Nutzen.pdf"
    if not panel_pdf.exists():
        raise SystemExit(f"--panel needs Nutzen.pdf in {folder}")
    boards, outline = panel_drawings(panel_pdf)
    all_points = np.concatenate(segments(outline) + [s for b in boards for s in segments(b)])
    origin_pdf = all_points.min(axis=0)
    scale, offset = coarse_fit(lines, all_points - origin_pdf, frame_bbox)
    scale, offset, error = refine_fit(lines, all_points - origin_pdf, scale, offset)
    if error > 3.0:
        warnings.append(f"Panel outline fits the image poorly (mean distance {error:.1f} px), check the preview")

    def to_image(pt):
        return (np.array(pt, float) - origin_pdf) * scale + offset

    # NestShape = orientation of the top left board, NestShape_180 = boards turned by 180 degrees.
    reference = min(boards, key=lambda b: (round(b["rect"].y0), round(b["rect"].x0)))
    ref_direction = centroid_offset(reference)
    groups = {"NestShape": [], "NestShape_180": []}
    for b in boards:
        groups["NestShape" if np.dot(centroid_offset(b), ref_direction) >= 0 else "NestShape_180"].append(b)
    groups = {sid: group for sid, group in groups.items() if group}
    nest_shapes = {sid: (nest_polygon(group[0], origin_pdf, scale, offset, gray.shape), group[0]["rect"])
                   for sid, group in groups.items()}

    page = pymupdf.open(str(pdf_path))[0]
    to_board, variant, match = locate_board(page, reference)
    if match < MIN_BOARD_MATCH:
        warnings.append(f"Board found in {pdf_path.name} only {match:.0%} on its lines, check the preview")
    size = np.array((reference["rect"].width, reference["rect"].height))
    chips = []
    for _, rect in colored_boxes(page.get_drawings()):
        corners = np.array([to_board((rect.x0, rect.y0)), to_board((rect.x1, rect.y1))])
        if (corners.min(axis=0) >= 0).all() and (corners.max(axis=0) <= size).all():
            chips.append((rect, corners))
    if len(chips) != 1:
        raise SystemExit(f"--panel supports exactly one MCU box per board, found {len(chips)} in {pdf_path.name}")
    rect, chip = chips[0]
    name = chip_reference(page, rect, page.get_text("words")) or "1"
    mcu_shape = args.shape_names.get(name, "MCU_" + name.replace(" ", "_"))
    chip_w, chip_h = snap(abs(chip[1][0] - chip[0][0]) * scale), snap(abs(chip[1][1] - chip[0][1]) * scale)
    mcu_coords = [(0, 0), (chip_w, 0), (chip_w, chip_h), (0, chip_h), (0, 0)]

    labels = ocr_words(gray)
    nests = []
    for shape_id, group in groups.items():
        poly, first = nest_shapes[shape_id]
        local = chip if shape_id == "NestShape" else size - chip
        for b in group:
            r = b["rect"]
            shift = np.round(np.array((r.x0 - first.x0, r.y0 - first.y0)) * scale).astype(int)
            contour = (np.array(poly) + shift).astype(np.int32)
            inside = [text for text, c, _ in labels
                      if cv2.pointPolygonTest(contour, (float(c[0]), float(c[1])), False) >= 0]
            pcb = [int(m.group(1)) for m in map(PCB_LABEL.match, inside) if m]
            progs = [parse_legend(t)[0][0] for t in inside if PROGRAMMER_TOKEN.match(t)]
            nest = {"nestId": pcb[0] if len(pcb) == 1 else None,
                    "shape": placement(shape_id, *(np.array(poly[0]) + shift)), "mcus": []}
            if len(progs) == 1:
                p = progs[0]
                x0, y0 = np.array([to_image((r.x0 + x, r.y0 + y)) for x, y in local]).min(axis=0)
                nest["mcus"].append({"mcuId": 1, "shape": placement(mcu_shape, snap(x0), snap(y0)),
                                     "programmer": {"programmerId": p[0], "channel": p[1], "slot": p[2]}})
            else:
                warnings.append(f"Board at PDF ({r.x0:.0f}, {r.y0:.0f}) has {len(progs)} PROG labels, no MCU added")
            nests.append(nest)
    next_id = max([n["nestId"] for n in nests if n["nestId"] is not None], default=0) + 1
    for nest in nests:
        if nest["nestId"] is None:
            warnings.append(f"Board without a single PCB label got nestId {next_id}, check the preview")
            nest["nestId"] = next_id
            next_id += 1
    ids = [n["nestId"] for n in nests]
    if len(set(ids)) != len(ids):
        warnings.append("Duplicate PCB labels, nest IDs are not unique")
    nests.sort(key=lambda n: n["nestId"])

    shapes = [(sid, [(x - origin[0], y - origin[1]) for x, y in poly]) for sid, (poly, _) in nest_shapes.items()]
    shapes.append((mcu_shape, mcu_coords))
    report = [f"Fit:      panel scale {scale:.4f} px/pt, offset ({offset[0]:.1f}, {offset[1]:.1f}), "
              f"mean error {error:.2f} px",
              f"Boards:   {', '.join(f'{len(g)} {sid}' for sid, g in groups.items())}; {pdf_path.name} shows the "
              f"board {variant} ({match:.0%} on lines), MCU '{name}'",
              f"Nests:    {len(nests)}, MCUs: {sum(len(n['mcus']) for n in nests)}"]
    for n in nests:
        for m in n["mcus"]:
            p = m["programmer"]
            report.append(f"  PCB{n['nestId']}: {n['shape']['shapeId']}, MCU at ({m['shape']['shapeXCoord']}, "
                          f"{m['shape']['shapeYCoord']}) programmer {p['programmerId']}/{p['channel']}/{p['slot']}")
    return shapes, nests, report


# ---------- output ----------

def write_json(doc, path):
    text = json.dumps(doc, indent=2).replace("\n", "\r\n")
    path.write_text(text, encoding="utf-8", newline="")


def draw_preview(image_path, doc, path):
    image = cv2.imdecode(np.fromfile(str(image_path), np.uint8), cv2.IMREAD_COLOR)
    shapes = {s["shapeId"]: s["coordinates"] for s in doc["shapes"]}

    def draw(place, color):
        coords = shapes[place["shapeId"]]
        dx, dy = place["shapeXCoord"] - coords[0]["x"], place["shapeYCoord"] - coords[0]["y"]
        pts = np.array([(c["x"] + dx, c["y"] + dy) for c in coords], np.int32)
        if len({tuple(p) for p in pts}) <= 2:
            cv2.circle(image, tuple(int(v) for v in pts[0]), 30, color, -1)
        else:
            cv2.polylines(image, [pts], False, color, 12)

    draw(doc["shape"], (255, 0, 0))
    for nest in doc["nests"]:
        draw(nest["shape"], (0, 0, 255))
        for mcu in nest["mcus"]:
            draw(mcu["shape"], (0, 200, 0))
    path.parent.mkdir(parents=True, exist_ok=True)
    small = cv2.resize(image, None, fx=1 / 3, fy=1 / 3, interpolation=cv2.INTER_AREA)
    cv2.imencode(".png", small)[1].tofile(str(path))


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder", type=Path, help="fixture input folder")
    parser.add_argument("--name", required=True, help="output name (ask the user)")
    parser.add_argument("--fixture-id", type=int, default=None, help="fixture ID if there is no 'FX ID<n>.txt'")
    parser.add_argument("--pdf", type=Path, default=None,
                        help="MCU position PDF if there is no MCU_Pos.pdf (relative to the folder or absolute)")
    parser.add_argument("--out-dir", type=Path, default=None, help="default: <folder>/../../output")
    parser.add_argument("--force", action="store_true", help="overwrite an existing JSON")
    parser.add_argument("--scale", type=float, default=1.0, help="scale for a newly created resource image")
    parser.add_argument("--shape-name", action="append", default=[], metavar="NAME=ID",
                        help="shape id for a legend name, e.g. 'IO driver=UART'")
    parser.add_argument("--point-name", action="append", default=None, metavar="NAME",
                        help="legend names drawn as a point (default: 'IO driver')")
    parser.add_argument("--panel", action="store_true",
                        help="Nutzen.pdf has several equal boards: one nest per board, nest IDs and programmers "
                             "from the PCB<n> / PROG<id><A|B><slot> labels in the image (OCR)")
    parser.add_argument("--preview", type=Path, default=None, help="default: preview/<Name>.png next to this script")
    args = parser.parse_args(argv)
    args.shape_names = dict(item.split("=", 1) for item in args.shape_name)
    args.point_names = set(args.point_name or ["IO driver"])
    return args


def main(argv=None):
    args = parse_args(argv)
    folder = args.folder.resolve()
    name = args.name
    out_dir = (args.out_dir or folder.parent.parent / "output").resolve()
    json_path = out_dir / f"{name}.json"
    if json_path.exists() and not args.force:
        raise SystemExit(f"{json_path} exists. Use --force to overwrite or --out-dir for another folder.")
    fixture = fixture_id(folder, args.fixture_id)
    pdf_path = mcu_pdf(folder, args.pdf)

    image_path, created = resource_image(folder, out_dir, name, args.scale)
    gray = read_gray(image_path)
    lines = (gray < DARK_THRESHOLD).astype(np.uint8)
    origin = (gray.shape[1] // 2, gray.shape[0] // 2)
    warnings = []
    frame_poly = frame_polygon(gray, lines, warnings)

    if args.panel:
        shapes, nests, report = build_panel(folder, pdf_path, gray, lines, bounding_box(frame_poly),
                                            origin, args, warnings)
    else:
        shapes, nests, report = build_single(pdf_path, gray, lines, frame_poly, origin, args, warnings)

    doc = {
        "fixtureId": fixture,
        "image": f"fixtures/resources/{image_path.name}",
        "imagetype": "gray",
        "shape": placement("ShapeModel", *frame_poly[0]),
        "shapes": [shape_entry("ShapeModel", [(x - origin[0], y - origin[1]) for x, y in frame_poly])]
                  + [shape_entry(sid, coords) for sid, coords in shapes],
        "nests": nests,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(doc, json_path)
    preview = args.preview or Path(__file__).resolve().parent / "preview" / f"{name}.png"
    draw_preview(image_path, doc, preview)

    print(f"PDF:      {pdf_path.name}")
    print(f"Image:    {image_path}{' (created)' if created else ''}")
    for line in report:
        print(line)
    print(f"JSON:     {json_path}")
    print(f"Preview:  {preview}")
    for warning in warnings:
        print(f"WARNING:  {warning}")


if __name__ == "__main__":
    sys.exit(main())
