"""Render docs/diagrams/*.mmd to rendered/*.svg and rendered/*.png.

    python docs/diagrams/render.py                  # every diagram
    python docs/diagrams/render.py 04-erd-overview  # one, by name

Needs mmdc (@mermaid-js/mermaid-cli) on PATH, Node and Pillow. Every PNG
comes out 1600 px wide on white. mmdc's --size caps the longer side, so the
size passed is worked out from the SVG's own proportions, and Pillow corrects
the last few pixels of rounding.

Afterwards it prints how large each diagram's text would print if the SVG
were placed at full A4 text width: 15.9 cm, which is A4 less the guidelines'
1-inch margins. "body" is the size most of the text is set in, "min" the
smallest any text uses (measure.cjs). A landscape page has 24.6 cm, so multiply
by 24.6 / 15.9 for one placed there.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
OUT = HERE / "rendered"
CONFIG = HERE / "mermaid.config.json"
WIDTH = 1600

# A gantt lays itself out to the viewport width, so its text size relative to
# the figure is set here: a narrower viewport prints larger. Its PNG is drawn
# at the same viewport and scaled up, so the two renders match.
GANTT_VIEWPORTS = {"07-gantt": 1000}

PRINT_WIDTH_CM = 15.9
POINTS_PER_CM = 72 / 2.54

# architecture-beta diagrams draw AWS icons from this Iconify pack.
ICON_PACKS = ["@iconify-json/logos"]


def mmdc(
    source: Path, target: Path, *, size: int | None = None, scale: float = 1
) -> None:
    command = [
        "mmdc", "-q",
        "-i", str(source),
        "-o", str(target),
        "-c", str(CONFIG),
        "-b", "white",
    ]
    if size is not None:
        command += ["--size", str(size)]
    if scale != 1:
        command += ["--scale", str(scale)]
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

    if name in GANTT_VIEWPORTS:
        viewport = GANTT_VIEWPORTS[name]
        mmdc(source, svg, size=viewport)
        mmdc(source, png, size=viewport, scale=WIDTH / viewport)
    else:
        # For every diagram but a gantt this only caps the SVG's max-width.
        mmdc(source, svg, size=WIDTH)
        width, height = svg_proportions(svg)
        longer_side = WIDTH if width >= height else round(WIDTH * height / width)
        mmdc(source, png, size=longer_side)

    image = Image.open(png)
    if image.width != WIDTH:
        new_height = round(image.height * WIDTH / image.width)
        image.resize((WIDTH, new_height), Image.Resampling.LANCZOS).save(png)
    print(f"{name}: {WIDTH} px wide")


def node_path() -> str:
    """mmdc's own node_modules, where measure.cjs finds puppeteer."""
    npm_root = subprocess.run(
        ["npm", "root", "-g"],
        check=True, capture_output=True, text=True, shell=sys.platform == "win32",
    ).stdout.strip()
    return str(Path(npm_root) / "@mermaid-js" / "mermaid-cli" / "node_modules")


def print_sizes(names: list[str]) -> None:
    svgs = [str(OUT / f"{name}.svg") for name in names]
    measured = subprocess.run(
        ["node", str(HERE / "measure.cjs"), *svgs],
        check=True, capture_output=True, text=True,
        env={**os.environ, "NODE_PATH": node_path()},
    ).stdout

    print()
    print(f"{'diagram':<26}  at {PRINT_WIDTH_CM:g} cm: body / min")
    for line in measured.splitlines():
        result = json.loads(line)
        points_per_unit = PRINT_WIDTH_CM * POINTS_PER_CM / result["viewBoxWidth"]
        body = result["body"] * points_per_unit
        smallest = result["min"] * points_per_unit
        print(f"{Path(result['file']).stem:<26}  {body:15.1f} / {smallest:3.1f} pt")


def main() -> None:
    OUT.mkdir(exist_ok=True)
    names = sys.argv[1:] or sorted(path.stem for path in HERE.glob("[0-9]*.mmd"))
    for name in names:
        render(name)
    print_sizes(names)


if __name__ == "__main__":
    main()
