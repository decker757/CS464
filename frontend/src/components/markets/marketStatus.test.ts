import { describe, expect, it } from 'vitest'
import { formatCloseCountdown, formatCloseTime, statusDisplay, tradingStopped } from './marketStatus'

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

// #382. Neither input may switch off the clock half ADR 0011 makes the authority.
describe('tradingStopped', () => {
  // No close time is no clock: stopped, as the backend's trading_is_open says.
  it('reads an open market with no close time as stopped, as the backend does', () => {
    expect(tradingStopped('open', null, NOW)).toBe(true)
  })

  // Invalid Date compares false against everything, so unguarded this read as trading.
  it('reads an unparseable close time as stopped rather than still trading', () => {
    expect(tradingStopped('open', '', NOW)).toBe(true)
  })
})

// [X-1] #431 AC 3. Three of the seven statuses are named after a step of the
// resolution machinery rather than after anything a trader can act on:
// `pending_resolution` and `approved` are both "the result is being decided",
// one of them only because a second administrator has to agree (ADR 0016),
// and an approved result still goes back to CLOSED if it is sent back inside
// its dispute window (ADR 0023) — so only `settled` is final. The admin's own
// screens keep the precise words, which is why those are the default.
describe('statusDisplay', () => {
  it.each([
    { status: 'pending_resolution', toAdmins: 'Pending',  toTraders: 'Resolving' },
    { status: 'approved',           toAdmins: 'Approved', toTraders: 'Awaiting payout' },
    { status: 'settled',            toAdmins: 'Settled',  toTraders: 'Resolved' },
  ])('calls a $status market "$toAdmins" to an admin and "$toTraders" to a trader', ({ status, toAdmins, toTraders }) => {
    expect(statusDisplay(status).label).toBe(toAdmins)
    expect(statusDisplay(status, 'admin').label).toBe(toAdmins)
    expect(statusDisplay(status, 'trader').label).toBe(toTraders)
  })

  // The words both sides already share, and the ones the trader's filter and
  // the admin's tabs have in common.
  it('leaves the statuses that need no translation alone', () => {
    expect(statusDisplay('open', 'trader')).toEqual(statusDisplay('open'))
    expect(statusDisplay('closed', 'trader')).toEqual(statusDisplay('closed'))
  })

  // A status this frontend has not heard of yet is still shown rather than
  // crashing the page, in whosever words were asked for.
  it('shows a status it does not know by its own name', () => {
    expect(statusDisplay('sent_back', 'trader')).toEqual({ label: 'sent back', tone: 'neutral' })
  })
})
