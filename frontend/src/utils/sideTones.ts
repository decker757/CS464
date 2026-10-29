/** Which colour an outcome is drawn in on a browse card ([X-1] #441). */
export type SideTone = 'yes' | 'no' | 'navy' | 'gold'

const BRAND_TONES: SideTone[] = ['navy', 'gold']

/**
 * Each outcome's tone, in the order of `labels`.
 *
 * Green and red only for a market whose two outcomes are Yes and No, in
 * either order and any case: the pairing the trading panel already uses for
 * Buy and Sell. Green and red say more than "a different side" — they say yes
 * and no — and on "Lakers" against "Celtics" that would make one team the
 * answer and the other the refusal. Every other outcome takes the brand's two
 * tones, alternating by position, which tell neighbours apart without taking
 * a side.
 */
export function sideTonesOf(labels: string[]): SideTone[] {
  const normalised = labels.map(label => label.trim().toLowerCase())
  const isYesNo = normalised.length === 2 && normalised.includes('yes') && normalised.includes('no')
  return normalised.map((label, index) => {
    if (isYesNo) return label === 'yes' ? 'yes' : 'no'
    return BRAND_TONES[index % BRAND_TONES.length]
  })
}
