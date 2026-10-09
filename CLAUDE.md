# CLAUDE.md

## Purpose

Generates fixture config JSON files (plus resource image) for a programming fixture viewer from
fixture input data: a contour image of the fixture/PCB and a PCB drawing PDF with the MCUs marked.
Everything needed to create a new output without an example is described here.

## Repository

| File | Purpose |
|---|---|
| `fixture_gen.py` | Generator: input folder → `<Name>.json`, resource image if missing, preview PNG |
| `pdf_inspect.py` | Shows what the generator reads from an `MCU_Pos*.pdf` (board path, MCU boxes, legend, references) |
| `install_requirements.ps1` | Installs `requirements.txt` into the user's Python (3.10+) and checks the imports |
| `requirements.txt` | numpy, opencv-python-headless, pymupdf (pinned) |

Setup: `powershell -ExecutionPolicy Bypass -File .\install_requirements.ps1`

The fixture data is not in this repo. Known data location: `C:\DEV\Test\New folder (4)\input\...` and `...\output\...`.

## Input folder

`input/<NN>_<Name with spaces>/`, e.g. `03_Stellantis Small`, `05_VCC SPA1 Volvo ECU`.

| File | Content |
|---|---|
| `FX ID<n>.txt` | Empty. The fixture ID is the number in the file name. If the folder has no fixture ID (e.g. only `No FX ID.txt`), always ask the user for it and pass it with `--fixture-id`. |
| `Nutzen_CNT.jpg` | Contour line drawing (gray/black lines on white) of the PCB in its nest, with labels PCB1, PROGxx, B5/B7, DMC. Same orientation as the PDF (not mirrored). |
| `MCU_Pos.pdf` | Valeo PCB assembly drawing (vector). MCUs are filled boxes in color (yellow, green, purple ...). Optional legend right of the board: colored box + text like `PROG2A W35N`, `PROG1A / 1B1 XTDA`. |
| `MCU_Pos_ADJUSTED.pdf` | Optional. Corrected version of MCU_Pos.pdf and wins over it. Corrections are red strike-throughs of legend text, new legend text, and red callout notes (e.g. "hier nur punkt (IO driver), kein rechteck" = the IO driver is a point, not a rectangle). |
| `Nutzen.pdf` | Optional outline drawing (PCB + connector). Not used. |
| other PDF | Seen: `bad4200_01_ASSEMBLY_BOT_ETL000_B.pdf` (folder `Nutzen`, no MCU_Pos.pdf). Valeo bottom-side assembly drawing with the same colored MCU boxes and a legend in short form `P1A TC37x`, `P1B W25N01`. It had the same orientation as the CNT image (not mirrored). Check the holes every time anyway. |

If the folder has no `MCU_Pos.pdf` (or `MCU_Pos*ADJUSTED*.pdf`), always ask the user whether one of the
other PDFs in the folder should be used instead. List them in the question. Never pick one yourself.
Pass the chosen PDF with `--pdf`.

## Output

- `output/<Name>.json`. Always ask the user for `<Name>` and pass it with `--name`. Never derive it from
  the folder name. (Existing names: `Stellantis_Small`, `VCC_SPA1_Volvo_ECU`.) The default output folder is `<folder>/../../output`
  (layout `input/<folder>` next to `output/`); for any other layout, pass `--out-dir`.
- `output/resources/<Name>.jpg` (or `.png`): the resource image. Existing resource images are used as they are.
  If missing, the generator creates it: `Nutzen_CNT.jpg`, optionally downscaled (`--scale`), a 40 px white
  margin around it (the board can touch the CNT image border, e.g. Nutzen), and a black 5 px frame 12 px
  inside the new image border. (VCC: CNT 16458x7785 was downscaled by 1/3.125 to 5267x2491.
  Stellantis: same size 7016x4728. Both got a hand-drawn black frame.)

### JSON format

Formatting: 2-space indent, CRLF line endings, UTF-8 without BOM, no trailing newline. Integers for all
coordinates, `0.0` for `shapeXCoordinate`/`shapeYCoordinate`, `null` for `backgroundColor`.

```json
{
  "fixtureId": 19,
  "image": "fixtures/resources/Stellantis_Small.jpg",
  "imagetype": "gray",
  "shape": { "shapeId": "ShapeModel", "shapeXCoord": 50, "shapeYCoord": 14 },
  "shapes": [
    { "shapeId": "ShapeModel", "borderThickness": 3,
      "coordinates": [ { "x": -3458, "y": -2350 }, "... frame rectangle, closed ..." ],
      "backgroundColor": null, "shapeXCoordinate": 0.0, "shapeYCoordinate": 0.0 },
    { "shapeId": "NestShape", "borderThickness": 3, "coordinates": [ "... PCB outline, closed ..." ],
      "backgroundColor": null, "shapeXCoordinate": 0.0, "shapeYCoordinate": 0.0 },
    { "shapeId": "MCU_U800", "borderThickness": 3,
      "coordinates": [ {"x":0,"y":0}, {"x":1080,"y":0}, {"x":1080,"y":1080}, {"x":0,"y":1080}, {"x":0,"y":0} ],
      "backgroundColor": null, "shapeXCoordinate": 0.0, "shapeYCoordinate": 0.0 }
  ],
  "nests": [
    { "nestId": 1,
      "shape": { "shapeId": "NestShape", "shapeXCoord": 110, "shapeYCoord": 124 },
      "mcus": [
        { "mcuId": 1,
          "shape": { "shapeId": "MCU_U800", "shapeXCoord": 5250, "shapeYCoord": 490 },
          "programmer": { "programmerId": 1, "channel": 0, "slot": 0 } }
      ] }
  ]
}
```

| Field | Meaning |
|---|---|
| `fixtureId` | Number from `FX ID<n>.txt` |
| `image` | Always `fixtures/resources/<resource file name>` |
| `imagetype` | Always `"gray"` |
| `shape` | Placement of `ShapeModel` (the fixture frame / panel outline) |
| `shapes[]` | Shape definitions, each `shapeId` once. Shapes are reused by several placements (e.g. both halves of a dual-programmer MCU). |
| `borderThickness` | Always 3 |
| `nests[]` | One nest per PCB. All seen fixtures have one nest, `nestId` 1. |
| `nests[].mcus[]` | One entry per programmer connection. `mcuId` 1..n. |
| `programmer` | From the legend token, see below |

### Coordinate rules (verified against the hand-made VCC JSON)

- All values are pixels of the resource image (x right, y down).
- Shape coordinates are relative to a free reference point. The generator uses the image centre.
  (VCC's hand-made file used ≈ (2060, 907); it doesn't matter.)
- A placement `shapeXCoord`/`shapeYCoord` is the image position of the shape's **first coordinate**:
  `image point = coordinate − coordinates[0] + (shapeXCoord, shapeYCoord)`.
- Polygons are closed: the last point repeats the first.
- MCU rectangles start at (0,0), so their placement is the top-left corner.
- A point shape (IO driver) is a degenerate polygon `[p, p + (0,5), p]`, placed at the chip centre.

### How each element is derived

| Element | Rule |
|---|---|
| `ShapeModel` | Centre line of the black frame of the resource image. Order TL → BL → BR → TR → TL. A frame counts only if all four sides are continuous dark lines (≥ 98% coverage). If no frame (panel outline) is found, create one: the outer contour of the drawing in the resource image, simplified with 2 px tolerance, starting at the top of the left edge like the NestShape. The generator does this automatically and prints a warning; tell the user. |
| `NestShape` | PCB outline only (board path from the PDF, mapped to image pixels), **not** the connector housing. Starts at the top of the left edge and runs down the left side (counter-clockwise on screen). Simplified with 2 px tolerance. |
| MCU box | Filled colored (non-gray) box inside the PDF board outline. Colored boxes outside the board are legend boxes. |
| MCU name | Legend text minus the programmer tokens (`XTDA`, `W35N`, `IO driver`). Without a legend: the reference designator inside the box (`U800`). |
| `shapeId` | `MCU_<name>` with spaces → `_`, unless overridden (`--shape-name "IO driver=UART"`; VCC used `UART`). |
| Programmer | Token `PROG<id><A/B><slot?>`, `P<id><A/B><slot?>` (e.g. `P1A TC37x`) or short `<id><A/B><slot?>` after a `/`: `programmerId` = id, `channel` A=0 / B=1, `slot` = the digit, 0 if missing (user decision 2026-10-09). `PROG1A / 1B1 XTDA` = one chip on two programmer channels. |
| Several programmers on one chip | Box split horizontally into equal parts, left part = first token. |
| No legend | Programmer 1, channel 0, slot 0 (PROG1A), next chips 2A, 3A ... The generator prints a warning. Check the PROGxx labels in `Nutzen_CNT.jpg`. |
| Point instead of rectangle | Legend names in `--point-name` (default `IO driver`). |
| MCU size and position | Exact chip box from the PDF, rounded to 10 px. |
| `mcuId` order | Sorted by (programmerId, channel, slot). |
| Crossed-out text | Words crossed by a red horizontal line in the PDF are ignored. |

### PDF → image mapping

The PDF board outline has the same shape and aspect ratio as the PCB contour in `Nutzen_CNT.jpg`
(VCC 2.185, Stellantis 2.377). The generator finds the board path (largest black stroked path,
both sides > 10% of the page), then fits scale and offset:

1. Coarse: template matching of the outline on the image downscaled to 1200 px width, 150 scales
   between 0.4 and 1.0 of the largest scale that fits inside the frame.
2. Fine: coordinate descent on (distance to nearest line − Gaussian-blurred line intensity), which centres
   the outline on the 5 px thick lines.

Dark threshold for lines: gray < 160 (JPEG anti-aliasing).

## Workflow for a new fixture

1. `python pdf_inspect.py "<input folder>/MCU_Pos_ADJUSTED.pdf"` (or `MCU_Pos.pdf`). Check: one board
   outline with plausible aspect ratio, one colored box per MCU, legend parsed correctly.
2. Read the PDF and `Nutzen_CNT.jpg` yourself (Read tool) for notes and callouts the parser doesn't
   understand (points instead of rectangles, renamed MCUs, changed programmer channels).
3. `python fixture_gen.py "<input folder>" --name <Name from the user> [--fixture-id <n>] [--pdf <file>] [--shape-name "NAME=ID"] [--point-name "NAME"] [--scale 0.32]`.
   It refuses to overwrite an existing JSON; use `--force` only with the user's OK, or `--out-dir` to test.
4. Look at the preview `preview/<Name>.png` (blue = ShapeModel, red = NestShape, green = MCUs; points
   drawn as dots). The red outline must lie on the PCB contour and green boxes where the colored chips are in the PDF.
5. Ask the user about anything not covered by the rules above (MCU names without a legend, nest including the connector, slots).
6. After runs or question rounds that produced new findings (rules, user decisions, new input
   variants, pitfalls, reference values), always ask the user whether to add them to this CLAUDE.md.
   Show the proposed text and add it only after the user agrees.

## Reference values (regression)

| Fixture | ID | Image | Fit scale (px/pt) | Offset | Nest pts | MCUs |
|---|---|---|---|---|---|---|
| Stellantis Small | 19 | 7016x4728 | 20.6891 | (109.7, 41.3) | 21 | MCU_U800 1080x1080 at (5250, 490), 1/0/0 |
| VCC SPA1 Volvo ECU | 20 | 5267x2491 | 10.2397 | (51.0, 88.1) | 47 | MCU_XTDA 330x650 at (3520, 760) 1/0/0 and (3850, 760) 1/1/1; UART point (1778, 735) 1/1/0; MCU_W35N 230x180 at (4590, 1220) 2/0/0 (run with `--shape-name "IO driver=UART"`) |
| Renault P10 Main Master | 33 (from the user, no FX ID file) | 2410x1519 (created: CNT 2330x1439 + 40 px margin) | 7.9959 | (69.5, 52.9) | 46 | MCU_TC37x 380x380 at (1080, 470) 1/0/0; MCU_W25N01 180x140 at (730, 690) 1/1/0 |

The Renault run: `C:\DEV\Test\New folder (2)\Nutzen`, `--name Renault_P10_Main_Master --fixture-id 33
--pdf bad4200_01_ASSEMBLY_BOT_ETL000_B.pdf --out-dir output`.

No-frame test (same call, `Nutzen_CNT.jpg` used directly as resource image, so no frame): ShapeModel from
the outer contour, 46 points, placed at (28, 51); fit 7.9956 px/pt, offset (29.6, 13.0);
MCU_TC37x at (1040, 430), MCU_W25N01 at (690, 650).

VCC from `C:\DEV\Test\New folder (2)` (same input files, no resource image, `--scale 1.0`): image
16458x7785 created, scale 31.9924 px/pt, offset (161.6, 277.8), 78 nest points, XTDA at (10990, 2360) /
(12020, 2360), UART (5558, 2299), W35N (14350, 3810), i.e. the 0.32 values / 0.32 within a few px.
This was created before the 40 px margin existed. Regenerating it now gives a 16538x7865 image and all values +40.

`output/Stellantis_Small.json` was produced by this generator and must stay byte-identical when
regenerated from the same input and resource image.

`output/VCC_SPA1_Volvo_ECU.json` was hand-made and differs from the generator on purpose or by mistake:
MCU rectangles smaller than the chips (XTDA 2x 250x500 at (3510, 870)/(3760, 870), W35N 140x110 at (4560, 1270)),
UART point at (1250, 870) instead of the IO driver chip centre, slots from the original PDF
(XTDA 1B slot 0, UART slot 1; by the slot rule above it should be XTDA slot 1, UART slot 0),
an unused `PMIC` shape and a different `mcuId` order. Don't use it as the reference for these details.

## Pitfalls

- Packages are installed with `pip --user`. `python -I` disables the user site and the imports fail.
  Don't run the scripts with `-I`.
- Import PyMuPDF as `pymupdf`; `import fitz` is deprecated. PyMuPDF is AGPL-licensed (fine for this internal tool).
- `get_drawings()` rects include the stroke. The generator uses path points for the board origin.
- Windows PowerShell 5.1 turns native stderr (pip notices) into errors under `$ErrorActionPreference = "Stop"`.
- A missing resource image is created silently. When testing with `--out-dir`, copy `output/resources/` there
  first, otherwise the fit is done on a newly created image and differs slightly.
- Watermark text in the PDFs (e.g. yellow "Prototype Released" over the board) is not picked up as an MCU box.
  Only filled colored boxes count.
- Not every fixture's drawing must be in the same orientation as the CNT image. Check a mounting hole
  in the PDF against the CNT image. The generator doesn't handle mirrored or rotated drawings.
