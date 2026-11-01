// Measures the text in rendered SVGs, for render.py's print-size estimate.
//
//   node measure.cjs rendered/01-system-architecture.svg ...
//
// Prints one JSON line per file: the viewBox width, the font size that most of
// the text is set in (body), and the smallest any visible text uses (min), all
// in SVG units. Mermaid sets sizes in several places (theme, per-diagram CSS,
// inline attributes), so this asks the browser rather than reading the CSS.
// Needs puppeteer, which mmdc already installs; render.py sets NODE_PATH.

const fs = require("fs");
const puppeteer = require("puppeteer");

function measureInPage() {
  const root = document.querySelector("svg");
  const viewBoxWidth = Number(root.getAttribute("viewBox").trim().split(/[\s,]+/)[2]);
  root.setAttribute("width", viewBoxWidth);
  root.removeAttribute("height");
  // mmdc --size writes max-width or max-height; either would shrink the text.
  root.style.maxWidth = "none";
  root.style.maxHeight = "none";

  const charactersBySize = new Map();
  for (const element of root.querySelectorAll("*")) {
    let ownText = "";
    for (const node of element.childNodes) {
      if (node.nodeType === Node.TEXT_NODE) ownText += node.textContent;
    }
    ownText = ownText.trim();
    if (ownText === "") continue;
    const box = element.getBoundingClientRect();
    if (box.width === 0 || box.height === 0) continue;

    // Text inside a foreignObject is sized in that object's coordinates.
    const holder = element.closest("foreignObject") || element;
    const matrix = holder.getScreenCTM ? holder.getScreenCTM() : null;
    const scale = matrix ? Math.hypot(matrix.a, matrix.b) : 1;
    const size = Math.round(parseFloat(getComputedStyle(element).fontSize) * scale * 10) / 10;
    charactersBySize.set(size, (charactersBySize.get(size) || 0) + ownText.length);
  }

  let body = null;
  let mostCharacters = -1;
  for (const [size, characters] of charactersBySize) {
    if (characters > mostCharacters) {
      body = size;
      mostCharacters = characters;
    }
  }
  return { viewBoxWidth, body, min: Math.min(...charactersBySize.keys()) };
}

async function main() {
  const browser = await puppeteer.launch({ headless: true });
  const page = await browser.newPage();
  for (const file of process.argv.slice(2)) {
    const svg = fs.readFileSync(file, "utf8");
    await page.setContent(`<!doctype html><body style="margin:0">${svg}</body>`);
    const result = await page.evaluate(measureInPage);
    console.log(JSON.stringify({ file, ...result }));
  }
  await browser.close();
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
