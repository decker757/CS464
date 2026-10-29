import { describe, expect, it } from 'vitest'
import { inRounds } from './rounds'

// #454: trades spread over time, so each market's price history has a shape.
// The seed pauses between rounds, so a market whose trades all landed in one
// round would draw a single step instead of a line.

describe('inRounds', () => {
  it('puts the nth trade of every market in round n, keeping each market in order', () => {
    const markets = [['a1', 'a2', 'a3'], ['b1'], ['c1', 'c2']]

    expect(inRounds(markets)).toEqual([['a1', 'b1', 'c1'], ['a2', 'c2'], ['a3']])
  })
})
