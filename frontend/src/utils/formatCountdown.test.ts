import { describe, expect, it } from 'vitest'
import { disputeWindowOpen, formatCountdown } from './formatCountdown'

// The absolute half is locale- and timezone-formatted (market-service.md's
// dispute-screen notes, point 1, wants it beside the relative time because
// the browser's clock can be wrong), so the expectation is built the same
// way the function builds it rather than hardcoded for one timezone —
// this file must pass in any timezone the test runner happens to be in.
function absolute(iso: string): string {
  return new Date(iso).toLocaleString('en-SG', { weekday: 'short', hour: '2-digit', minute: '2-digit' })
}

describe('formatCountdown', () => {
  it('shows hours and minutes when under a day remains', () => {
    const now = new Date('2026-09-17T06:00:00Z')
    const endsAt = '2026-09-17T11:12:00Z'
    expect(formatCountdown(endsAt, now)).toBe(`5h 12m (${absolute(endsAt)})`)
  })

  it('shows days and hours when a day or more remains', () => {
    const now = new Date('2026-09-17T06:00:00Z')
    const endsAt = '2026-09-19T09:00:00Z'
    expect(formatCountdown(endsAt, now)).toBe(`2d 3h (${absolute(endsAt)})`)
  })

  it('shows minutes only when under an hour remains', () => {
    const now = new Date('2026-09-17T10:50:00Z')
    const endsAt = '2026-09-17T11:12:00Z'
    expect(formatCountdown(endsAt, now)).toBe(`22m (${absolute(endsAt)})`)
  })

  it('floors to 0m rather than going negative once the window has passed', () => {
    const now = new Date('2026-09-17T12:00:00Z')
    const endsAt = '2026-09-17T11:12:00Z'
    expect(formatCountdown(endsAt, now)).toBe(`0m (${absolute(endsAt)})`)
  })
})

describe('disputeWindowOpen', () => {
  const now = new Date('2026-09-17T06:00:00Z')

  it('is true while the stored end is still in the future', () => {
    expect(disputeWindowOpen('2026-09-17T11:12:00Z', now)).toBe(true)
  })

  it('is false once the stored end has passed', () => {
    expect(disputeWindowOpen('2026-09-17T05:00:00Z', now)).toBe(false)
  })

  it('is false for a market with no stored window end', () => {
    // Never approved, or sent back and not yet re-approved (market-service.md).
    expect(disputeWindowOpen(null, now)).toBe(false)
  })
})
