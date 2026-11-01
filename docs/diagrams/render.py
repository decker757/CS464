"""Render docs/diagrams/*.mmd to rendered/*.svg and rendered/*.png.

    python docs/diagrams/render.py                  # every diagram
    python docs/diagrams/render.py 04-erd-overview  # one, by name

Needs mmdc (@mermaid-js/mermaid-cli) on PATH, Node and Pillow. Every PNG
comes out 1600 px wide on white. mmdc's --size caps the longer side, so the
size passed is worked out from the SVG's own proportions, and Pillow corrects
the last few pixels of rounding.

Afterwards it prints how large each diagram's text would print if the SVG
were placed at full A4 text width. 17 cm is the width the proposal is laid out
at; 15.9 cm is A4 less the guidelines' 1-inch margins. "body" is the size most
of the text is set in, "min" the smallest any text uses (measure.cjs). A
landscape page has 24.6 cm, so multiply by 24.6 / 17 for one placed there.
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

PRINT_WIDTHS_CM = (17.0, 15.9)
POINTS_PER_CM = 72 / 2.54

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

    header = "".join(f"  at {cm:g} cm: body / min" for cm in PRINT_WIDTHS_CM)
    print()
    print(f"{'diagram':<26}{header}")
    for line in measured.splitlines():
        result = json.loads(line)
        row = ""
        for cm in PRINT_WIDTHS_CM:
            points_per_unit = cm * POINTS_PER_CM / result["viewBoxWidth"]
            body = result["body"] * points_per_unit
            smallest = result["min"] * points_per_unit
            row += f"  {body:13.1f} / {smallest:3.1f} pt"
        print(f"{Path(result['file']).stem:<26}{row}")


def main() -> None:
    OUT.mkdir(exist_ok=True)
    names = sys.argv[1:] or sorted(path.stem for path in HERE.glob("[0-9]*.mmd"))
    for name in names:
        render(name)
    print_sizes(names)


if __name__ == "__main__":
    main()
