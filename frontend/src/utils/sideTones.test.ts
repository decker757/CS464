import { describe, expect, it } from 'vitest'
import { sideTonesOf } from './sideTones'

// [X-1] #441's colour rule. Green and red say yes and no, so only a market
// whose two outcomes are Yes and No gets them; anything else would make one
// side "the answer" and the other "the refusal".
describe('sideTonesOf', () => {
  it.each([
    { labels: ['Yes', 'No'], tones: ['yes', 'no'] },
    { labels: ['No', 'Yes'], tones: ['no', 'yes'] },
    { labels: [' YES ', 'no'], tones: ['yes', 'no'] },
  ])('colours Yes and No green and red, in any order and case: $labels', ({ labels, tones }) => {
    expect(sideTonesOf(labels)).toEqual(tones)
  })

  it('gives any other pair the brand tones, by position', () => {
    expect(sideTonesOf(['Lakers', 'Celtics'])).toEqual(['navy', 'gold'])
  })

  // Yes and No among others are not a yes-or-no question.
  it('gives a market with Yes and No among more outcomes the brand tones too', () => {
    expect(sideTonesOf(['Yes', 'No', 'Maybe'])).toEqual(['navy', 'gold', 'navy'])
  })
})
