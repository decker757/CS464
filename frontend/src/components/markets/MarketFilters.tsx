import RadioPillGroup, { type RadioPillOption } from '../ui/RadioPillGroup'
import SearchForm from '../ui/SearchForm'
import { statusDisplay } from './marketStatus'

// The statuses GET /public/markets accepts, in the order market-service.md
// lists them; draft and submitted are never listed to a trader.
const STATUS_FILTERS = ['open', 'closed', 'pending_resolution', 'approved', 'settled'] as const
export type StatusFilter = (typeof STATUS_FILTERS)[number]

// The trader's words, not the lifecycle's: "Pending" and "Approved" are
// steps of a state machine that exists for a rule a trader knows nothing
// about, and nothing on this screen explained the difference between
// Approved and Settled ([X-1] #431). marketStatus.ts holds the mapping and
// why the administrator's list keeps its own words.
function filterLabel(status: StatusFilter | null): string {
  return status === null ? 'All' : statusDisplay(status, 'trader').label
}

// null stands for "All", alongside every real status, so the pill list and
// its label are drawn from one place instead of "All" being copied in by hand.
const FILTER_OPTIONS: RadioPillOption<StatusFilter | null>[] = [null, ...STATUS_FILTERS].map(status => ({
  value: status,
  label: filterLabel(status),
}))

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
  const appliedFilterCount = [query !== '', status !== null].filter(Boolean).length
  const hasActiveFilters = appliedFilterCount > 0
  // One filter is cleared by its own chip, so "Clear all" is for more than one
  // filter (#442).
  const showClearAll = appliedFilterCount > 1

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

      {/* Choosing a status replaces the last one, so the pills are a radio
          group rather than toggle buttons (#442). */}
      <RadioPillGroup label="Filter by status" options={FILTER_OPTIONS} selected={status} onSelect={onStatusChange} />

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
