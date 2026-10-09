"""Generate a fixture config JSON from a fixture input folder.

Input folder (e.g. input/03_Stellantis Small):
  FX ID<n>.txt          fixture ID in the file name (else --fixture-id)
  Nutzen_CNT.jpg        contour image, source of the resource image
  MCU_Pos[_ADJUSTED].pdf  PCB drawing, MCUs filled in color, optional legend "PROG1A / 1B1 XTDA"
                        or "P1A TC37x" (else --pdf <other PDF>)

Output:
  <out-dir>/<Name>.json            fixture config
  <out-dir>/resources/<Name>.jpg   resource image (only created if missing: CNT image, 40 px white margin, frame)
  preview/<Name>.png               overlay of all shapes on the image, for a visual check

How it works:
  - ShapeModel = black frame of the resource image.
  - The board outline path and the colored MCU boxes are read as vectors from the PDF.
  - Scale and offset PDF -> image are fitted by matching the PDF board outline to the image contour.
  - NestShape = PDF board outline in image pixels. MCUs = colored boxes, split horizontally when one
    box has several programmers ("PROG1A / 1B1"), a point instead of a rectangle for --point-name.
  - Polygon coordinates are relative to the image centre. A placement (shapeXCoord/YCoord) is the
    image position of the shape's first coordinate.

Usage:
  python fixture_gen.py "<data>/input/03_Stellantis Small" --name Stellantis_Small
  python fixture_gen.py "<data>/input/05_VCC SPA1 Volvo ECU" --name VCC_SPA1_Volvo_ECU --shape-name "IO driver=UART" --out-dir C:/temp/check
  python fixture_gen.py "C:/data/Nutzen" --name Renault_P10_Main_Master --fixture-id 33 --pdf bad4200_01_ASSEMBLY_BOT_ETL000_B.pdf --out-dir C:/data/output
"""
import argparse
import json
import re
import sys
from pathlib import Path

import cv2
import numpy as np
import pymupdf

DARK_THRESHOLD = 160
MCU_GRID = 10
BORDER_THICKNESS = 3
FRAME_MARGIN = 40
STRIKE_COLOR_MIN_RED = 0.8
PROGRAMMER_TOKEN = re.compile(r"^(?:PROG|P)?(\d+)([AB])(\d?)$", re.IGNORECASE)
REFERENCE_DESIGNATOR = re.compile(r"^[A-Z]{1,2}\d+$")


# ---------- input folder ----------

def fixture_id(folder, given):
    if given is not None:
        return given
    for path in folder.glob("FX ID*.txt"):
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
    """Centre line of the outer black frame: (left, top, right, bottom)."""
    h, w = gray.shape
    row = dark_run_centres(gray[h // 2, :])
    col = dark_run_centres(gray[:, w // 2])
    if len(row) < 2 or len(col) < 2:
        raise SystemExit("Frame not found in resource image")
    return round(row[0]), round(col[0]), round(row[-1]), round(col[-1])


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
    """'PROG1A / 1B1 XTDA' -> ([(1, 0, 0), (1, 1, 1)], 'XTDA')."""
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
    return board, chips, legends, words


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
    """Start at the top of the left edge and run down the left side, like the VCC example."""
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
    parser.add_argument("--name", required=True, help="output name, e.g. Stellantis_Small (ask the user)")
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
    frame = detect_frame(gray)
    origin = (gray.shape[1] // 2, gray.shape[0] // 2)

    board, chips, legends, words = read_pdf(pdf_path)
    points = outline_points(board)
    origin_pdf = np.concatenate(segments(board)).min(axis=0)
    scale, offset = coarse_fit(lines, points, frame)
    scale, offset, error = refine_fit(lines, points, scale, offset)

    def to_image(pt):
        return tuple((np.array(pt) - origin_pdf) * scale + offset)

    warnings = []
    if error > 3.0:
        warnings.append(f"Board outline fits the image poorly (mean distance {error:.1f} px), check the preview")
    nest = nest_polygon(board, origin_pdf, scale, offset, gray.shape)
    frame_poly = [(frame[0], frame[1]), (frame[0], frame[3]), (frame[2], frame[3]), (frame[2], frame[1]),
                  (frame[0], frame[1])]
    mcu_shapes, mcus = build_mcus(chips, legends, words, to_image, origin, args, warnings)

    def relative(poly):
        return [(x - origin[0], y - origin[1]) for x, y in poly]

    doc = {
        "fixtureId": fixture,
        "image": f"fixtures/resources/{image_path.name}",
        "imagetype": "gray",
        "shape": placement("ShapeModel", *frame_poly[0]),
        "shapes": [shape_entry("ShapeModel", relative(frame_poly)), shape_entry("NestShape", relative(nest))]
                  + [shape_entry(sid, coords) for sid, coords in mcu_shapes.items()],
        "nests": [{"nestId": 1, "shape": placement("NestShape", *nest[0]), "mcus": mcus}],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(doc, json_path)
    preview = args.preview or Path(__file__).resolve().parent / "preview" / f"{name}.png"
    draw_preview(image_path, doc, preview)

    print(f"PDF:      {pdf_path.name}")
    print(f"Image:    {image_path}{' (created)' if created else ''}")
    print(f"Fit:      scale {scale:.4f} px/pt, offset ({offset[0]:.1f}, {offset[1]:.1f}), mean error {error:.2f} px")
    print(f"Nest:     {len(nest)} points, MCUs: {len(mcus)}")
    for mcu in mcus:
        p = mcu["programmer"]
        print(f"  MCU {mcu['mcuId']}: {mcu['shape']['shapeId']} at ({mcu['shape']['shapeXCoord']}, "
              f"{mcu['shape']['shapeYCoord']}) programmer {p['programmerId']}/{p['channel']}/{p['slot']}")
    print(f"JSON:     {json_path}")
    print(f"Preview:  {preview}")
    for warning in warnings:
        print(f"WARNING:  {warning}")


if __name__ == "__main__":
    sys.exit(main())
