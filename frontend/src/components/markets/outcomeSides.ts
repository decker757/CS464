import type { OutcomePrice } from '../../api/ledgerApi'
import type { PublicOutcome } from '../../api/marketApi'
import { sideTonesOf, type SideTone } from '../../utils/sideTones'

/** An outcome on a priced browse card: its price, and the tone it is drawn in. */
export interface PricedSide {
  outcome: PublicOutcome
  price: string
  tone: SideTone
}

// Each tone as text, as a bar segment, and as a chip's bottom edge. The text
// sits straight on the white card, where every one of these reaches AA
// (index.css.test.ts); the fills are shapes beside a label and a percentage
// that say the same thing, never the only signal (#441).
export const SIDE_TONE_TEXT: Record<SideTone, string> = {
  yes: 'text-success-strong',
  no: 'text-danger-strong',
  navy: 'text-smu-navy',
  gold: 'text-smu-gold-text',
}
export const SIDE_TONE_FILL: Record<SideTone, string> = {
  yes: 'bg-success',
  no: 'bg-danger',
  navy: 'bg-smu-navy',
  gold: 'bg-smu-gold',
}
export const SIDE_TONE_EDGE: Record<SideTone, string> = {
  yes: 'border-success',
  no: 'border-danger',
  navy: 'border-smu-navy',
  gold: 'border-smu-gold',
}

/** Every outcome with its price and tone, or null unless the snapshot prices
 *  them all: a split with a side missing would hand that side's share to the
 *  others. */
export function pricedSidesOf(outcomes: PublicOutcome[], prices: OutcomePrice[]): PricedSide[] | null {
  const tones = sideTonesOf(outcomes.map(outcome => outcome.label))
  const sides: PricedSide[] = []
  for (const [index, outcome] of outcomes.entries()) {
    const quoted = prices.find(price => price.outcome_id === outcome.id)
    if (!quoted) return null
    sides.push({ outcome, price: quoted.price, tone: tones[index] })
  }
  return sides
}
