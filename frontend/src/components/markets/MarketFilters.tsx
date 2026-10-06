import { useState, type FormEvent } from 'react'
import Button from '../ui/Button'
import TextInput from '../ui/TextInput'
import { selectableClass } from '../ui/selectableClass'
import { STATUS_CONFIG } from './marketStatus'

// The statuses GET /public/markets accepts; draft and submitted are never
// listed to a trader (market-service.md).
const STATUS_FILTERS = ['open', 'closed', 'pending_resolution', 'approved'] as const
export type StatusFilter = (typeof STATUS_FILTERS)[number]

interface MarketFiltersProps {
  /** The search that is applied to the list, not what is half typed in the box. */
  query: string
  status: StatusFilter | null
  onQueryChange: (query: string) => void
  onStatusChange: (status: StatusFilter | null) => void
}

function ActiveFilterChip({ label, clearLabel, onClear }: { label: string; clearLabel: string; onClear: () => void }) {
  return (
    <button
      type="button"
      onClick={onClear}
      aria-label={clearLabel}
      className="cursor-pointer rounded-full border border-smu-navy/20 px-3 py-1 text-xs font-semibold text-smu-navy hover:border-smu-navy/40"
    >
      {label} ×
    </button>
  )
}

export default function MarketFilters({ query, status, onQueryChange, onStatusChange }: MarketFiltersProps) {
  const [draft, setDraft] = useState(query)
  const hasActiveFilters = query !== '' || status !== null

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    onQueryChange(draft.trim())
  }

  function handleClearQuery() {
    setDraft('')
    onQueryChange('')
  }

  function handleClearAll() {
    handleClearQuery()
    onStatusChange(null)
  }

  return (
    <div className="mb-6 space-y-4">
      <form role="search" onSubmit={handleSubmit} className="flex gap-2">
        <label htmlFor="market-search" className="sr-only">Search markets</label>
        <TextInput
          id="market-search"
          type="search"
          value={draft}
          onChange={event => setDraft(event.target.value)}
          placeholder="Search markets by question"
        />
        <Button type="submit" variant="outline" size="md">Search</Button>
      </form>

      <div role="group" aria-label="Filter by status" className="flex flex-wrap gap-2">
        <button
          type="button"
          aria-pressed={status === null}
          onClick={() => onStatusChange(null)}
          className={`cursor-pointer rounded-full border px-4 py-1.5 text-[13px] font-semibold transition ${selectableClass(status === null)}`}
        >
          All
        </button>
        {STATUS_FILTERS.map(option => (
          <button
            key={option}
            type="button"
            aria-pressed={status === option}
            onClick={() => onStatusChange(option)}
            className={`cursor-pointer rounded-full border px-4 py-1.5 text-[13px] font-semibold transition ${selectableClass(status === option)}`}
          >
            {STATUS_CONFIG[option].label}
          </button>
        ))}
      </div>

      {hasActiveFilters && (
        <div role="group" aria-label="Active filters" className="flex flex-wrap items-center gap-2">
          {query !== '' && (
            <ActiveFilterChip label={`Search: ${query}`} clearLabel="Clear search" onClear={handleClearQuery} />
          )}
          {status !== null && (
            <ActiveFilterChip label={`Status: ${STATUS_CONFIG[status].label}`} clearLabel="Clear status filter" onClear={() => onStatusChange(null)} />
          )}
          <button type="button" onClick={handleClearAll} className="cursor-pointer text-xs font-semibold text-muted underline hover:text-smu-navy">
            Clear all
          </button>
        </div>
      )}
    </div>
  )
}
