import { useRef, type KeyboardEvent } from 'react'
import FilterPill from '../ui/FilterPill'
import SearchForm from '../ui/SearchForm'
import { statusDisplay } from './marketStatus'

// The statuses GET /public/markets accepts, in the order market-service.md
// lists them; draft and submitted are never listed to a trader.
const STATUS_FILTERS = ['open', 'closed', 'pending_resolution', 'approved', 'settled'] as const
export type StatusFilter = (typeof STATUS_FILTERS)[number]

// null stands for "All", alongside every real status, so the pill list and
// its label are drawn from one place instead of "All" being copied in by hand.
const FILTER_OPTIONS: (StatusFilter | null)[] = [null, ...STATUS_FILTERS]

// The trader's words, not the lifecycle's: "Pending" and "Approved" are
// steps of a state machine that exists for a rule a trader knows nothing
// about, and nothing on this screen explained the difference between
// Approved and Settled ([X-1] #431). marketStatus.ts holds the mapping and
// why the administrator's list keeps its own words.
function filterLabel(status: StatusFilter | null): string {
  return status === null ? 'All' : statusDisplay(status, 'trader').label
}

// The arrow keys of WAI-ARIA's radio group pattern: Right and Down choose the
// next option, Left and Up the previous one, wrapping at either end. Any other
// key is not the group's, so the answer is null.
function arrowTargetIndex(key: string, index: number, count: number): number | null {
  if (key === 'ArrowRight' || key === 'ArrowDown') {
    return (index + 1) % count
  }
  if (key === 'ArrowLeft' || key === 'ArrowUp') {
    return (index - 1 + count) % count
  }
  return null
}

interface MarketFiltersProps {
  /** The search that is applied to the list, not what is half typed in the box. */
  query: string
  status: StatusFilter | null
  onQueryChange: (query: string) => void
  onStatusChange: (status: StatusFilter | null) => void
}

// `pointer-coarse:` on the size, like FilterPill: the chip a finger clears has
// to be 44px, and the one a mouse clears does not have to be ([X-1] #364).
function ActiveFilterChip({ label, clearLabel, onClear }: { label: string; clearLabel: string; onClear: () => void }) {
  return (
    <button
      type="button"
      onClick={onClear}
      aria-label={clearLabel}
      className="cursor-pointer rounded-full border border-smu-navy/20 px-3 py-1 text-xs font-semibold text-smu-navy hover:border-smu-navy/40 pointer-coarse:min-h-11 pointer-coarse:px-4"
    >
      {label} ×
    </button>
  )
}

export default function MarketFilters({ query, status, onQueryChange, onStatusChange }: MarketFiltersProps) {
  const pillRefs = useRef<(HTMLButtonElement | null)[]>([])
  const hasActiveFilters = query !== '' || status !== null
  // One filter is cleared by its own chip, so "Clear all" is for two (#442).
  const showClearAll = query !== '' && status !== null

  // Choosing a status replaces the last one, so the pills are a radio group
  // rather than toggle buttons (#442). Native radios would bring the arrow
  // keys for free but not FilterPill's look, so the pattern is written here:
  // the arrows move the choice and the focus together, and only the chosen
  // pill is in the tab order. preventDefault keeps the arrows from scrolling.
  // An arrow with Alt, Ctrl or Cmd held is the browser's or the screen
  // reader's (Alt+Left is Back), so the group leaves it alone.
  function handleStatusKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (event.altKey || event.ctrlKey || event.metaKey) {
      return
    }
    const targetIndex = arrowTargetIndex(event.key, index, FILTER_OPTIONS.length)
    if (targetIndex === null) {
      return
    }
    event.preventDefault()
    onStatusChange(FILTER_OPTIONS[targetIndex])
    pillRefs.current[targetIndex]?.focus()
  }

  function handleClearAll() {
    onQueryChange('')
    onStatusChange(null)
  }

  return (
    <div className="mb-6 space-y-4">
      <SearchForm
        id="market-search"
        label="Search markets"
        placeholder="Search markets by question"
        query={query}
        onSearch={onQueryChange}
      />

      <div role="radiogroup" aria-label="Filter by status" className="flex flex-wrap gap-2">
        {FILTER_OPTIONS.map((option, index) => (
          <FilterPill
            key={option ?? 'all'}
            ref={element => {
              pillRefs.current[index] = element
            }}
            active={status === option}
            role="radio"
            aria-checked={status === option}
            tabIndex={status === option ? 0 : -1}
            onClick={() => onStatusChange(option)}
            onKeyDown={event => handleStatusKeyDown(event, index)}
          >
            {filterLabel(option)}
          </FilterPill>
        ))}
      </div>

      {hasActiveFilters && (
        <div role="group" aria-label="Active filters" className="flex flex-wrap items-center gap-2">
          {query !== '' && (
            <ActiveFilterChip label={`Search: ${query}`} clearLabel="Clear search" onClear={() => onQueryChange('')} />
          )}
          {status !== null && (
            <ActiveFilterChip label={`Status: ${filterLabel(status)}`} clearLabel="Clear status filter" onClear={() => onStatusChange(null)} />
          )}
          {showClearAll && (
            <button type="button" onClick={handleClearAll} className="cursor-pointer text-xs font-semibold text-muted underline hover:text-smu-navy pointer-coarse:min-h-11 pointer-coarse:px-2">
              Clear all
            </button>
          )}
        </div>
      )}
    </div>
  )
}
