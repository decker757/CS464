import { describe, expect, it } from 'vitest'
import { formatCloseCountdown, formatCloseTime, tradingStopped } from './marketStatus'

const MINUTE = 60_000
const HOUR = 60 * MINUTE

const NOW = new Date('2026-10-18T12:00:00Z')
const closingIn = (ms: number) => new Date(NOW.getTime() + ms).toISOString()

// [X-1] #364 AC 2. A market stops accepting trades the instant close_time
// passes, and the CLOSED status is only written a few seconds later by the
// sweep (ADR 0011), so what this says is the trader's only honest signal of
// whether a market can still be traded. The boundaries are where it changes
// its mind, so they are what is pinned here.
describe('formatCloseCountdown', () => {
  it.each([
    { msLeft: 48 * HOUR - 1,                     label: 'Closes in 47h 59m' },
    { msLeft: 3 * HOUR + 12 * MINUTE + 59_000,   label: 'Closes in 3h 12m' },
    { msLeft: HOUR,                              label: 'Closes in 1h 0m' },
    { msLeft: HOUR - 1,                          label: 'Closes in 59m' },
    { msLeft: 9 * MINUTE,                        label: 'Closes in 9m' },
    { msLeft: MINUTE,                            label: 'Closes in 1m' },
    { msLeft: MINUTE - 1,                        label: 'Closes in under a minute' },
    { msLeft: 1,                                 label: 'Closes in under a minute' },
    { msLeft: 0,                                 label: 'Closed' },
    { msLeft: -1,                                label: 'Closed' },
  ])('reads "$label" with $msLeft ms left', ({ msLeft, label }) => {
    expect(formatCloseCountdown('open', closingIn(msLeft), NOW)).toBe(label)
  })

  // The far end of the window: 48 hours out there is nothing a countdown tells
  // a trader that the date does not, so the card says exactly what it said
  // before this ticket.
  it('shows the date rather than a countdown 48 hours out', () => {
    const closeTime = closingIn(48 * HOUR)
    expect(formatCloseCountdown('open', closeTime, NOW)).toBe(formatCloseTime('open', closeTime, NOW))
    expect(formatCloseCountdown('open', closeTime, NOW)).not.toMatch(/in/)
  })

  // The clock is not the only way trading stops: an admin can close a market
  // early, and its close_time stays in the future.
  it('reads Closed on a market closed early, whose close time has not passed', () => {
    expect(formatCloseCountdown('closed', closingIn(3 * HOUR), NOW)).toBe('Closed')
  })
})

// #382. The clock half of tradingStopped is the half ADR 0011 makes the
// authority, so neither of these may switch it off by accident.
describe('tradingStopped', () => {
  // No close time means no clock exists to have passed, so the status alone
  // answers. Deliberately so: new Date(null) is the 1970 epoch, which would
  // read every such market as stopped.
  it('answers from the status alone when there is no close time', () => {
    expect(tradingStopped('open', null, NOW)).toBe(false)
  })

  // A close time that does not parse is a clock that cannot be read, not one
  // that has not passed. Invalid Date compares false against everything, so
  // without a guard this read as still trading, and the admin row offered
  // Close on a market the clock may already have stopped (ADR 0014).
  it('reads an unparseable close time as stopped rather than still trading', () => {
    expect(tradingStopped('open', '', NOW)).toBe(true)
  })
})
