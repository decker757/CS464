import { describe, expect, it } from 'vitest'
import type { PricePoint } from '../api/ledgerApi'
import { originalPriceKey, toChartRows, withLiveFrame } from './priceHistoryChart'

describe('toChartRows', () => {
  it('turns each point into one row at its time, naming every outcome both as a percentage and its original string', () => {
    const points: PricePoint[] = [
      { state_version: 0, occurred_at: '2026-01-01T00:00:00Z', prices: [{ outcome_id: 'o-yes', position: 0, price: '0.5000' }, { outcome_id: 'o-no', position: 1, price: '0.5000' }] },
      { state_version: 1, occurred_at: '2026-01-02T00:00:00Z', prices: [{ outcome_id: 'o-yes', position: 0, price: '0.7000' }, { outcome_id: 'o-no', position: 1, price: '0.3000' }] },
    ]

    const rows = toChartRows(points)

    expect(rows).toHaveLength(2)
    // Milliseconds since 1970, written out rather than derived with
    // Date.parse, so the test does not share the code's own conversion.
    expect(rows[0]).toMatchObject({
      time: 1767225600000,
      'o-yes': 50,
      'o-no': 50,
      [originalPriceKey('o-yes')]: '0.5000',
      [originalPriceKey('o-no')]: '0.5000',
    })
    expect(rows[1]).toMatchObject({
      time: 1767312000000,
      'o-yes': 70,
      'o-no': 30,
    })
  })

  it('keeps the points in the order given, oldest first, rather than re-sorting', () => {
    // Points already arrive oldest first (ledgerApi.ts); this only has to
    // not reshuffle them, not re-derive the order itself.
    const points: PricePoint[] = [
      { state_version: 5, occurred_at: '2026-01-05T00:00:00Z', prices: [{ outcome_id: 'o-yes', position: 0, price: '0.9000' }] },
      { state_version: 1, occurred_at: '2026-01-01T00:00:00Z', prices: [{ outcome_id: 'o-yes', position: 0, price: '0.1000' }] },
    ]

    const rows = toChartRows(points)

    expect(rows.map(row => row.time)).toEqual([1767571200000, 1767225600000])
  })
})

describe('withLiveFrame', () => {
  function point(version: number, yes: string, at = '2026-10-27T10:00:00Z'): PricePoint {
    return {
      state_version: version,
      occurred_at: at,
      prices: [
        { outcome_id: 'o-yes', position: 0, price: yes },
        { outcome_id: 'o-no', position: 1, price: '0.5000' },
      ],
    }
  }

  // #495's first criterion: the chart adds a point for every live frame.
  it('appends a frame newer than the history', () => {
    const merged = withLiveFrame([point(0, '0.5000'), point(1, '0.6000')], point(2, '0.7000'))

    expect(merged.map(p => p.state_version)).toEqual([0, 1, 2])
    expect(merged.at(-1)?.prices[0].price).toBe('0.7000')
  })

  // #495's second: a frame whose state_version the chart holds is skipped, so
  // a reconnect's refetch never draws the same trade twice.
  it('drops a frame the history already holds', () => {
    const history = [point(0, '0.5000'), point(1, '0.6000')]

    const merged = withLiveFrame(history, point(1, '0.9999'))

    expect(merged).toBe(history)
  })

  // The ledger publishes in state_version order and the socket refuses a
  // frame that is not newer, so an older one here is a late arrival the chart
  // has moved past. Splicing it in would redraw a line already read.
  it('drops a frame older than the newest point', () => {
    const history = [point(0, '0.5000'), point(5, '0.6000')]

    expect(withLiveFrame(history, point(3, '0.4000'))).toBe(history)
  })

  // ledger-service.md: "Dedupe on state_version, never on time." The same
  // trade carries one time on its point and another on its frame, and across
  // replicas the gap is clock skew — so a later clock must not make a
  // duplicate look new.
  it('dedupes on the version even when the frame carries a later time', () => {
    const history = [point(2, '0.6000', '2026-10-27T10:00:00Z')]

    const merged = withLiveFrame(history, point(2, '0.9999', '2026-10-27T10:00:09Z'))

    expect(merged).toBe(history)
  })

  // The socket has nothing yet on first paint, which is the ordinary case:
  // the history stands on its own.
  it('leaves the history alone when there is no frame', () => {
    const history = [point(0, '0.5000')]

    expect(withLiveFrame(history, null)).toBe(history)
  })

  // A market whose book has just opened has no history to compare against.
  it('takes a frame as the first point when the history is empty', () => {
    expect(withLiveFrame([], point(0, '0.5000')).map(p => p.state_version)).toEqual([0])
  })
})
