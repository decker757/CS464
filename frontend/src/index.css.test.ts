// Vitest stubs a CSS import to an empty string, so the stylesheet is read off
// disk. `tsconfig.json` does not list `node` in `types`; this file needs it.
/// <reference types="node" />
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

// The colour tokens in `index.css`, measured. WCAG 2.1 asks for 4.5:1 between
// text under 18px and whatever is behind it, and every size in this app is
// under 18px. The ratios are read out of the stylesheet rather than written
// down here, so lightening a token fails this file. [#360]
// https://www.w3.org/TR/WCAG21/#contrast-minimum
const AA_SMALL_TEXT = 4.5

/** An sRGB colour, each channel 0–1. */
type Colour = [number, number, number]

function parseHex(hex: string): Colour {
  const digits = hex.replace('#', '')
  return [0, 2, 4].map((at) => parseInt(digits.slice(at, at + 2), 16) / 255) as Colour
}

/** Every `--color-*` token declared in the `@theme` block, by name. */
function readColourTokens(): Record<string, Colour> {
  // Vitest runs from the frontend root, beside `vite.config.ts`.
  const stylesheet = readFileSync('src/index.css', 'utf8')
  const tokens: Record<string, Colour> = {}
  for (const [, name, hex] of stylesheet.matchAll(/--color-([a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;/g)) {
    tokens[name] = parseHex(hex)
  }
  return tokens
}

function relativeLuminance([red, green, blue]: Colour): number {
  const linearise = (channel: number) =>
    channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
  return 0.2126 * linearise(red) + 0.7152 * linearise(green) + 0.0722 * linearise(blue)
}

function contrastRatio(foreground: Colour, background: Colour): number {
  const [lighter, darker] = [relativeLuminance(foreground), relativeLuminance(background)]
    .sort((a, b) => b - a)
  return (lighter + 0.05) / (darker + 0.05)
}

/** What `bg-<colour>/<alpha>` actually looks like once it is over a surface. */
function tintOver(colour: Colour, surface: Colour, alpha: number): Colour {
  return colour.map((channel, at) => channel * alpha + surface[at] * (1 - alpha)) as Colour
}

const tokens = readColourTokens()
const WHITE: Colour = [1, 1, 1]
const SURFACES: Record<string, Colour> = {
  'a white card': WHITE,
  'the cream page': tokens['smu-cream'],
}

// Every token the app puts text in. Each is held to AA on both surfaces, so a
// token that moves to a new screen is already safe there.
const TEXT_TOKENS = [
  'smu-navy',
  'smu-gold-text',
  'secondary',
  'muted',
  'subtle',
  'danger',
  'danger-strong',
  'success',
  'success-strong',
  'warning',
  'warning-strong',
  'info',
  'info-strong',
]

// Tokens that are never text: the two page surfaces, hairline borders, a bar
// track, and the brand gold, which is 3.20:1 on cream and is the whole reason
// `--color-smu-gold-text` exists.
const SURFACE_ONLY = ['smu-gold', 'smu-cream', 'line', 'track', 'surface-muted']

// `Badge`: 11px bold text on a 10% tint of its own tone, on either surface.
// The same danger and success pairs at the same 10% are also TradingPanel's
// active Buy/Sell toggle and the trade and placed-order confirmation
// banners, so this table measures those too (#380).
const BADGE_TONES = [
  { tone: 'danger', text: 'danger-strong', tint: 'danger' },
  { tone: 'success', text: 'success-strong', tint: 'success' },
  { tone: 'neutral', text: 'secondary', tint: 'muted' },
  { tone: 'warning', text: 'warning-strong', tint: 'warning' },
  { tone: 'info', text: 'info-strong', tint: 'info' },
]

// Every other place in the app that puts a tone's own text on a tint of
// itself and that the `Badge` table does not already measure, each at the
// tint it is actually used at and the surfaces it actually sits on. A base
// tone reaches only 4.1–4.5:1 on its own tint at these strengths, short of
// AA, which is why each pairs with the darker `-strong` text instead (#380).
// Unlike `Badge` above, these are not all the same alpha, so each site names
// its own; most default to both surfaces, but gold-on-its-own-tint is only
// ever used inside a white `Card`, and on the cream page it is 4.43 — short
// of the line — so it does not claim cream.
const TONE_ON_OWN_TINT = [
  // AuthLayout's login/register error message.
  { site: 'the auth error message', text: 'danger-strong', tint: 'danger', alpha: 0.05 },
  // LandingFeatures' numbered pill and MarketPreviewCard's "Example" pill:
  // small bold gold text on a gold tint, which needs the darker
  // gold-for-text token rather than the brand gold itself — and both sit
  // inside a white Card, never straight on cream.
  { site: 'small gold text on a gold tint', text: 'smu-gold-text', tint: 'smu-gold', alpha: 0.1, surfaces: ['a white card'] },
]

// Every text on a browse chip while its price is marked as moved
// (OutcomeChips' movementTint): a 10% success or danger tint, inside a white
// Card. The label is neutral text rather than the tone's own, so neither table
// above measures it; muted measured 4.14–4.23:1 here, short of AA (#574).
const ON_A_MOVED_CHIP = [
  { site: 'the outcome label and the weekly change', text: 'secondary' },
  { site: 'the price and its arrow', text: 'smu-navy' },
]
const MOVED_CHIP_TINTS = ['success', 'danger']

// Without this, a token added later is simply absent from the lists above and
// nothing below ever measures it.
it('sorts every colour token into text or surface', () => {
  expect(Object.keys(tokens).sort()).toEqual([...TEXT_TOKENS, ...SURFACE_ONLY].sort())
})

describe('text on a page surface', () => {
  it.each(
    TEXT_TOKENS.flatMap((token) =>
      Object.keys(SURFACES).map((surface) => ({ token, surface })),
    ),
  )('$token on $surface', ({ token, surface }) => {
    expect(contrastRatio(tokens[token], SURFACES[surface])).toBeGreaterThanOrEqual(AA_SMALL_TEXT)
  })
})

describe('badge text on its own tint', () => {
  it.each(
    BADGE_TONES.flatMap((badge) =>
      Object.keys(SURFACES).map((surface) => ({ ...badge, surface })),
    ),
  )('$tone over its tint on $surface', ({ text, tint, surface }) => {
    const background = tintOver(tokens[tint], SURFACES[surface], 0.1)
    expect(contrastRatio(tokens[text], background)).toBeGreaterThanOrEqual(AA_SMALL_TEXT)
  })
})

// Every other known tone-on-own-tint pair in the app that `Badge`'s fixed 10%
// does not cover, each at the alpha it is actually used at (#380). Reading the
// alpha here, rather than hardcoding today's ratio, is what makes this fail if a
// site's tint is ever strengthened past the line, the same way the token
// table above fails if a token is lightened past it.
describe('a tone on its own tint, elsewhere in the app', () => {
  it.each(
    TONE_ON_OWN_TINT.flatMap((site) =>
      (site.surfaces ?? Object.keys(SURFACES)).map((surface) => ({ ...site, surface })),
    ),
  )('$site, on $surface', ({ text, tint, alpha, surface }) => {
    const background = tintOver(tokens[tint], SURFACES[surface], alpha)
    expect(contrastRatio(tokens[text], background)).toBeGreaterThanOrEqual(AA_SMALL_TEXT)
  })
})

describe('text on a browse chip that has just moved', () => {
  it.each(
    ON_A_MOVED_CHIP.flatMap((site) => MOVED_CHIP_TINTS.map((tint) => ({ ...site, tint }))),
  )('$site, on the $tint tint', ({ text, tint }) => {
    const background = tintOver(tokens[tint], WHITE, 0.1)
    expect(contrastRatio(tokens[text], background)).toBeGreaterThanOrEqual(AA_SMALL_TEXT)
  })
})

// `buttonClass.ts` disables a button by replacing its variant with a neutral
// plate rather than dimming it, so that it stops reading as a working button
// [#452]. That only holds if the label on the plate is still readable:
// "disabled" must not mean "illegible".
describe('a disabled button', () => {
  it('reads secondary text on the light plate', () => {
    expect(contrastRatio(tokens['secondary'], tokens['track'])).toBeGreaterThanOrEqual(AA_SMALL_TEXT)
  })

  // The on-navy variants have no light plate to use, so there the plate is a
  // 10% white wash over the navy and the label is white at 60%.
  it('reads a dimmed white on the wash over navy', () => {
    const plate = tintOver(WHITE, tokens['smu-navy'], 0.1)
    const label = tintOver(WHITE, plate, 0.6)
    expect(contrastRatio(label, plate)).toBeGreaterThanOrEqual(AA_SMALL_TEXT)
  })
})

describe('the gold button', () => {
  it('reads navy on the brand gold', () => {
    expect(contrastRatio(tokens['smu-navy'], tokens['smu-gold'])).toBeGreaterThanOrEqual(AA_SMALL_TEXT)
  })

  // Hovering lightens the gold to 90%, so the surface behind it shows through.
  it.each(Object.keys(SURFACES))('still reads on hover, over %s', (surface) => {
    const hovered = tintOver(tokens['smu-gold'], SURFACES[surface], 0.9)
    expect(contrastRatio(tokens['smu-navy'], hovered)).toBeGreaterThanOrEqual(AA_SMALL_TEXT)
  })
})
