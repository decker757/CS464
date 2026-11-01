// WCAG 2.1 contrast, shared by the token table in `index.css.test.ts` and the
// end-to-end check that reads colours off the real page (#574). WCAG asks for
// 4.5:1 between text under 18px and whatever is behind it, and every size in
// this app is under 18px.
// https://www.w3.org/TR/WCAG21/#contrast-minimum
export const AA_SMALL_TEXT = 4.5

/** An sRGB colour, each channel 0–1. */
export type Colour = [number, number, number]

function relativeLuminance([red, green, blue]: Colour): number {
  const linearise = (channel: number) =>
    channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
  return 0.2126 * linearise(red) + 0.7152 * linearise(green) + 0.0722 * linearise(blue)
}

export function contrastRatio(foreground: Colour, background: Colour): number {
  const [lighter, darker] = [relativeLuminance(foreground), relativeLuminance(background)]
    .sort((a, b) => b - a)
  return (lighter + 0.05) / (darker + 0.05)
}
