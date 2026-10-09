# CLAUDE.md

## Purpose

Generates fixture config JSON files (plus resource image) for a programming fixture viewer from
fixture input data: a contour image of the fixture/PCB and a PCB drawing PDF with the MCUs marked.
Everything needed to create a new output without an example is described here.

This file contains general rules only. Never store information about specific fixture configs here
(fixture names, IDs, file names, coordinates, measured values, data paths).

## Repository

| File | Purpose |
|---|---|
| `fixture_gen.py` | Generator: input folder → `<Name>.json`, resource image if missing, preview PNG |
| `pdf_inspect.py` | Shows what the generator reads from an MCU position PDF (board path, MCU boxes, legend, references) |
| `install_requirements.ps1` | Installs `requirements.txt` into the user's Python (3.10+) and checks the imports |
| `requirements.txt` | numpy, opencv-python-headless, pymupdf (pinned) |

Setup: `powershell -ExecutionPolicy Bypass -File .\install_requirements.ps1`

The fixture data is not in this repo. The user names the input folder and the output folder.

## Input folder

| File | Content |
|---|---|
| `FX ID<n>.txt` | Empty. The fixture ID is the number in the file name. If the folder has no fixture ID (e.g. `No FX ID.txt`), always ask the user for it and pass it with `--fixture-id`. An ID range (`FX ID<a>-<b>.txt`) stops the generator: always ask the user which ID to use and pass it with `--fixture-id`. |
| `Nutzen_CNT.jpg` | Contour line drawing (gray/black lines on white) of the PCB in its nest, with labels PCB1, PROGxx, B5/B7, DMC. Usually no frame. The board can touch the image border. |
| `MCU_Pos.pdf` | Valeo PCB assembly drawing (vector). MCUs are filled boxes in color. Optional legend next to the board: colored box + text, see Legend below. |
| `MCU_Pos_ADJUSTED.pdf` | Optional. Corrected version of MCU_Pos.pdf and wins over it. Corrections are red strike-throughs of legend text, new legend text, and red callout notes (e.g. "hier nur punkt (IO driver), kein rechteck" = this MCU is a point, not a rectangle). |
| `Nutzen.pdf` | Optional outline drawing of the panel (PCB, sometimes connector). Has no MCUs. Not used. |
| other PDFs | MCU position drawings can have other names, e.g. `MCU_Pos_Top.pdf` or `<drawing number>_ASSEMBLY_BOT_<...>.pdf` (bottom-side view). They work the same way. A bottom-side drawing can still have the same orientation as the CNT image; check it. |

If the folder has no `MCU_Pos.pdf` (or `MCU_Pos*ADJUSTED*.pdf`), always ask the user whether one of the
other PDFs in the folder should be used instead. List them in the question. Never pick one yourself.
Pass the chosen PDF with `--pdf`.

## Output

- `<out-dir>/<Name>.json`. Always ask the user for `<Name>` and pass it with `--name`. Never derive it from
  the folder name. The PDF title block (assembly board name, product) gives good suggestions.
  The default output folder is `<folder>/../../output` (layout `input/<folder>` next to `output/`);
  for any other layout, pass `--out-dir`.
- `<out-dir>/resources/<Name>.jpg` (or `.png`): the resource image. Existing resource images are used as they are.
  If missing, the generator creates it: `Nutzen_CNT.jpg`, optionally downscaled (`--scale`), a 40 px white
  margin around it, and a black 5 px frame 12 px inside the new image border.
  Hand-made resource images can be downscaled CNT images with a hand-drawn frame.

### JSON format

Formatting: 2-space indent, CRLF line endings, UTF-8 without BOM, no trailing newline. Integers for all
coordinates, `0.0` for `shapeXCoordinate`/`shapeYCoordinate`, `null` for `backgroundColor`.
Values below are placeholders.

```json
{
  "fixtureId": <n>,
  "image": "fixtures/resources/<Name>.jpg",
  "imagetype": "gray",
  "shape": { "shapeId": "ShapeModel", "shapeXCoord": <x>, "shapeYCoord": <y> },
  "shapes": [
    { "shapeId": "ShapeModel", "borderThickness": 3,
      "coordinates": [ { "x": <x>, "y": <y> }, "... frame rectangle or panel contour, closed ..." ],
      "backgroundColor": null, "shapeXCoordinate": 0.0, "shapeYCoordinate": 0.0 },
    { "shapeId": "NestShape", "borderThickness": 3, "coordinates": [ "... PCB outline, closed ..." ],
      "backgroundColor": null, "shapeXCoordinate": 0.0, "shapeYCoordinate": 0.0 },
    { "shapeId": "MCU_<type>", "borderThickness": 3,
      "coordinates": [ {"x":0,"y":0}, {"x":<w>,"y":0}, {"x":<w>,"y":<h>}, {"x":0,"y":<h>}, {"x":0,"y":0} ],
      "backgroundColor": null, "shapeXCoordinate": 0.0, "shapeYCoordinate": 0.0 }
  ],
  "nests": [
    { "nestId": 1,
      "shape": { "shapeId": "NestShape", "shapeXCoord": <x>, "shapeYCoord": <y> },
      "mcus": [
        { "mcuId": 1,
          "shape": { "shapeId": "MCU_<type>", "shapeXCoord": <x>, "shapeYCoord": <y> },
          "programmer": { "programmerId": 1, "channel": 0, "slot": 0 } }
      ] }
  ]
}
```

| Field | Meaning |
|---|---|
| `fixtureId` | Number from `FX ID<n>.txt`, or from the user |
| `image` | Always `fixtures/resources/<resource file name>` |
| `imagetype` | Always `"gray"` |
| `shape` | Placement of `ShapeModel` (the fixture frame / panel outline) |
| `shapes[]` | Shape definitions, each `shapeId` once. Shapes are reused by several placements (e.g. both halves of a dual-programmer MCU). |
| `borderThickness` | Always 3 |
| `nests[]` | One nest per PCB. So far always one nest, `nestId` 1. |
| `nests[].mcus[]` | One entry per programmer connection. `mcuId` 1..n. |
| `programmer` | From the legend token, see below |

### Coordinate rules

- All values are pixels of the resource image (x right, y down).
- Shape coordinates are relative to a free reference point. The generator uses the image centre.
  Hand-made files can use another reference point; it doesn't matter.
- A placement `shapeXCoord`/`shapeYCoord` is the image position of the shape's **first coordinate**:
  `image point = coordinate − coordinates[0] + (shapeXCoord, shapeYCoord)`.
- Polygons are closed: the last point repeats the first.
- MCU rectangles start at (0,0), so their placement is the top-left corner.
- A point shape is a degenerate polygon `[p, p + (0,5), p]`, placed at the chip centre.

### How each element is derived

| Element | Rule |
|---|---|
| `ShapeModel` | Centre line of the black frame of the resource image. Order TL → BL → BR → TR → TL. A frame counts only if all four sides are continuous dark lines (≥ 98% coverage). If no frame (panel outline) is found, create one: the outer contour of the drawing in the resource image, simplified with 2 px tolerance, starting at the top of the left edge like the NestShape. The generator does this automatically and prints a warning; tell the user. |
| `NestShape` | PCB outline only (board path from the PDF, mapped to image pixels), **not** the connector housing. Starts at the top of the left edge and runs down the left side (counter-clockwise on screen). Simplified with 2 px tolerance. |
| MCU box | Filled colored (non-gray) box inside the PDF board outline. Colored boxes outside the board are legend boxes. |
| Legend | Colored box with text in the same line. Programmer tokens and MCU type in any order: `PROG2A <type>`, `<type> PROG1A`, `PROG1A / 1B1 <type>`, `P1A <type>`. |
| Legend match | By exact fill color. A chip's fill can differ from its legend box (e.g. turquoise chip, light-cyan legend). Then the unused legend with the nearest hue is used and a warning is printed. Check that match in the PDF. |
| MCU name | Legend text minus the programmer tokens. Without a legend: the reference designator inside the box (e.g. `U123`). |
| `shapeId` | `MCU_<name>` with spaces → `_`, unless overridden with `--shape-name "<name>=<id>"` (e.g. `"IO driver=UART"`). |
| Programmer | Token `PROG<id><A/B><slot?>`, `P<id><A/B><slot?>` or short `<id><A/B><slot?>` after a `/`: `programmerId` = id, `channel` A=0 / B=1, `slot` = the digit, 0 if missing (user decision). `PROG1A / 1B1 <type>` = one chip on two programmer channels. |
| Several programmers on one chip | Box split horizontally into equal parts, left part = first token. |
| No legend | Programmer 1, channel 0, slot 0 (PROG1A), next chips 2A, 3A ... The generator prints a warning. Check the PROGxx labels in `Nutzen_CNT.jpg`. |
| Point instead of rectangle | Legend names in `--point-name` (default `IO driver`). |
| MCU size and position | Exact chip box from the PDF, rounded to 10 px. |
| `mcuId` order | Sorted by (programmerId, channel, slot). |
| Crossed-out text | Words crossed by a red horizontal line in the PDF are ignored. |

### PDF → image mapping

The PDF board outline has the same shape and aspect ratio as the PCB contour in `Nutzen_CNT.jpg`.
The generator finds the board path (largest black stroked path, both sides > 10% of the page),
then fits scale and offset:

1. Coarse: template matching of the outline on the image downscaled to 1200 px width, 150 scales
   between 0.4 and 1.0 of the largest scale that fits inside the frame (or panel contour).
2. Fine: coordinate descent on (distance to nearest line − Gaussian-blurred line intensity), which centres
   the outline on the line thickness.

Dark threshold for lines: gray < 160 (JPEG anti-aliasing).

## Workflow for a new fixture

1. `python pdf_inspect.py "<input folder>/<MCU PDF>"`. Check: one board outline with an aspect ratio
   like the PCB in the CNT image, one colored box per MCU, legend parsed correctly, notes about hue matches.
2. Read the PDF and `Nutzen_CNT.jpg` yourself (Read tool) for notes and callouts the parser doesn't
   understand (points instead of rectangles, renamed MCUs, changed programmer channels). Compare a
   mounting hole in both to make sure the drawing is not mirrored or rotated.
3. Ask the user in one round for everything open: name, fixture ID (missing or range), which PDF if
   there is no `MCU_Pos.pdf`, and anything not covered by the rules above.
4. `python fixture_gen.py "<input folder>" --name <Name> [--fixture-id <n>] [--pdf <file>] [--shape-name "NAME=ID"] [--point-name "NAME"] [--scale <f>] [--out-dir <dir>]`.
   It refuses to overwrite an existing JSON; use `--force` only with the user's OK.
5. Look at the preview `preview/<Name>.png` (blue = ShapeModel, red = NestShape, green = MCUs; points
   drawn as dots). The red outline must lie on the PCB contour and green boxes where the colored chips are in the PDF.
6. After runs or question rounds that produced new findings (rules, user decisions, new input
   variants, pitfalls), always ask the user whether to add them to this CLAUDE.md.
   Show the proposed text and add it only after the user agrees. Write findings as general rules,
   never as information about a specific fixture config.

## Verification after script changes

- Regenerate existing outputs with `--out-dir <temp dir>` and compare byte by byte with the originals
  (`cmp`). Copy the original resource images into `<temp dir>/resources/` first; otherwise a new image
  is created and the values differ.
- Outputs from older script versions can differ on purpose (e.g. resource images created before the
  40 px margin was added). Hand-made outputs are no reference for MCU sizes, point positions, slots or order.
- Look at the preview of each regenerated output.
- Test changed fallbacks directly, e.g. a resource image without a frame (copy `Nutzen_CNT.jpg` as the
  resource image) for the ShapeModel fallback.

## Pitfalls

- Packages are installed with `pip --user`. `python -I` disables the user site and the imports fail.
  Don't run the scripts with `-I`.
- Import PyMuPDF as `pymupdf`; `import fitz` is deprecated. PyMuPDF is AGPL-licensed (fine for this internal tool).
- `get_drawings()` rects include the stroke. The generator uses path points for the board origin.
- Windows PowerShell 5.1 turns native stderr (pip notices) into errors under `$ErrorActionPreference = "Stop"`.
- A missing resource image is created silently.
- Break-off tabs (mouse bites) on the board edge are part of the PDF board outline, so the NestShape can
  have 200+ points. That's expected.
- Watermark text in the PDFs (e.g. "Prototype Released" in yellow over the board) is not picked up as an MCU box.
  Only filled colored boxes count.
- Not every drawing must be in the same orientation as the CNT image. The generator doesn't handle
  mirrored or rotated drawings.
