"""Render docs/diagrams/*.mmd to rendered/*.svg and rendered/*.png.

    python docs/diagrams/render.py                  # every diagram
    python docs/diagrams/render.py 04-erd-overview  # one, by name

Needs mmdc (@mermaid-js/mermaid-cli) on PATH and Pillow. Every PNG comes out
1600 px wide on white. mmdc's --size caps the longer side, so the size passed
is worked out from the SVG's own proportions, and Pillow corrects the last
few pixels of rounding.
"""

import re
import subprocess
import sys
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
OUT = HERE / "rendered"
CONFIG = HERE / "mermaid.config.json"
WIDTH = 1600

# architecture-beta diagrams draw AWS icons from this Iconify pack.
ICON_PACKS = ["@iconify-json/logos"]


def mmdc(source: Path, target: Path, *, size: int | None = None) -> None:
    command = [
        "mmdc", "-q",
        "-i", str(source),
        "-o", str(target),
        "-c", str(CONFIG),
        "-b", "white",
    ]
    if size is not None:
        command += ["--size", str(size)]
    if "architecture-beta" in source.read_text(encoding="utf-8"):
        command += ["--iconPacks", *ICON_PACKS]
    subprocess.run(command, check=True, shell=sys.platform == "win32")


def svg_proportions(svg: Path) -> tuple[float, float]:
    text = svg.read_text(encoding="utf-8")
    match = re.search(r'viewBox="[-\d.]+ [-\d.]+ ([\d.]+) ([\d.]+)"', text)
    if match is None:
        raise SystemExit(f"{svg.name}: no viewBox to size the PNG from")
    return float(match.group(1)), float(match.group(2))


def render(name: str) -> None:
    source = HERE / f"{name}.mmd"
    svg = OUT / f"{name}.svg"
    png = OUT / f"{name}.png"

    # A gantt lays itself out to the viewport, so the SVG is drawn at the
    # target width too; for every other diagram this only caps max-width.
    mmdc(source, svg, size=WIDTH)
    width, height = svg_proportions(svg)
    longer_side = WIDTH if width >= height else round(WIDTH * height / width)
    mmdc(source, png, size=longer_side)

    image = Image.open(png)
    if image.width != WIDTH:
        new_height = round(image.height * WIDTH / image.width)
        image.resize((WIDTH, new_height), Image.Resampling.LANCZOS).save(png)
    print(f"{name}: {WIDTH} px wide")


def main() -> None:
    OUT.mkdir(exist_ok=True)
    names = sys.argv[1:] or sorted(path.stem for path in HERE.glob("[0-9]*.mmd"))
    for name in names:
        render(name)


if __name__ == "__main__":
    main()
