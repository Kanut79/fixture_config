"""Show what fixture_gen.py reads from an MCU_Pos PDF, to check a new fixture before generating.

Prints the board outline path, the colored boxes (MCU chips on the board, legend boxes outside),
the parsed legend lines with crossed-out words removed, and the reference designator inside each chip.

Usage:
  python pdf_inspect.py "<input folder>/MCU_Pos.pdf"
"""
import sys
from pathlib import Path

from fixture_gen import parse_legend, read_pdf, reference_inside


def fmt(rect):
    return f"({rect.x0:.1f}, {rect.y0:.1f}) - ({rect.x1:.1f}, {rect.y1:.1f}) [{rect.width:.1f} x {rect.height:.1f} pt]"


def inspect(path):
    board, chips, legends, words, notes = read_pdf(path)
    print(f"== {path}")
    print(f"Board outline: {fmt(board['rect'])}, {len(board['items'])} path items, "
          f"aspect {board['rect'].width / board['rect'].height:.3f}")
    print(f"MCU boxes on the board: {len(chips)}")
    for color, rect in sorted(chips, key=lambda c: (c[1].x0, c[1].y0)):
        programmers, name = parse_legend(legends.get(color, ""))
        print(f"  fill {color}: {fmt(rect)}")
        print(f"    reference: {reference_inside(rect, words) or '-'}")
        print(f"    legend:    '{legends.get(color, '')}' -> name '{name or '-'}', "
              f"programmers (id, channel, slot) {programmers or '-'}")
    unused = [c for c in legends if c not in {color for color, _ in chips}]
    for color in unused:
        print(f"  legend without MCU box: fill {color}: '{legends[color]}'")
    for note in notes:
        print(f"  NOTE: {note}")


def main(argv):
    if not argv:
        raise SystemExit(__doc__)
    for arg in argv:
        inspect(Path(arg))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
