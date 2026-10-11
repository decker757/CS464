import { describe, expect, it } from 'vitest'
import type { PricePoint } from '../api/ledgerApi'
import { originalPriceKey, toChartRows } from './priceHistoryChart'

describe('toChartRows', () => {
  it('turns each point into one row naming every outcome, both as a percentage and its original string', () => {
    const points: PricePoint[] = [
      { state_version: 0, occurred_at: '2026-01-01T00:00:00Z', prices: [{ outcome_id: 'o-yes', position: 0, price: '0.5000' }, { outcome_id: 'o-no', position: 1, price: '0.5000' }] },
      { state_version: 1, occurred_at: '2026-01-02T00:00:00Z', prices: [{ outcome_id: 'o-yes', position: 0, price: '0.7000' }, { outcome_id: 'o-no', position: 1, price: '0.3000' }] },
    ]

    const rows = toChartRows(points)

    expect(rows).toHaveLength(2)
    expect(rows[0]).toMatchObject({
      occurred_at: '2026-01-01T00:00:00Z',
      'o-yes': 50,
      'o-no': 50,
      [originalPriceKey('o-yes')]: '0.5000',
      [originalPriceKey('o-no')]: '0.5000',
    })
    expect(rows[1]).toMatchObject({
      occurred_at: '2026-01-02T00:00:00Z',
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

    expect(rows.map(row => row.occurred_at)).toEqual(['2026-01-05T00:00:00Z', '2026-01-01T00:00:00Z'])
  })

  it('returns an empty list for an empty market', () => {
    expect(toChartRows([])).toEqual([])
  })
})
