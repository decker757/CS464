// The closing-time presets the browse list offers (#442), in the order they
// are shown. Each is a calendar period in the trader's own timezone, which is
// why the browser works the window out: GET /public/markets takes instants,
// because the server does not know where the trader is (market-service.md,
// "The closing window").
export const CLOSING_PRESETS = ['today', 'this_week', 'this_month'] as const
export type ClosingPreset = (typeof CLOSING_PRESETS)[number]

const CLOSING_PRESET_LABELS: Record<ClosingPreset, string> = {
  today: 'Today',
  this_week: 'This week',
  this_month: 'This month',
}

/** A preset's name on its pill and its chip; null is "Any time", which sends no window. */
export function closingLabel(closing: ClosingPreset | null): string {
  return closing === null ? 'Any time' : CLOSING_PRESET_LABELS[closing]
}

/** A half-open window as the API takes it: `closesFrom` is in it, `closesBefore` is not. */
export interface ClosingWindow {
  closesFrom: string
  closesBefore: string
}

// A week starts on Monday here, as it does in Singapore. getDay() counts from
// Sunday, so Sunday is the last day of the week before, not the first of the
// next.
function daysSinceMonday(day: Date): number {
  return (day.getDay() + 6) % 7
}

/**
 * The local calendar day, Monday-to-Monday week or month that holds `now`,
 * from its first midnight to the midnight after it ends. Both ends are built
 * from the calendar rather than by adding hours, so a clock change inside the
 * window moves its end with it.
 */
export function closingWindowOf(preset: ClosingPreset, now: Date): ClosingWindow {
  const year = now.getFullYear()
  const month = now.getMonth()
  const date = now.getDate()

  let start: Date
  let end: Date
  if (preset === 'today') {
    start = new Date(year, month, date)
    end = new Date(year, month, date + 1)
  } else if (preset === 'this_week') {
    const monday = date - daysSinceMonday(now)
    start = new Date(year, month, monday)
    end = new Date(year, month, monday + 7)
  } else {
    start = new Date(year, month, 1)
    end = new Date(year, month + 1, 1)
  }

  return { closesFrom: start.toISOString(), closesBefore: end.toISOString() }
}
