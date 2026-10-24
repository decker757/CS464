import { useState } from 'react'
import AppLayout from '../components/layout/AppLayout'
import MarketFilters, { type StatusFilter } from '../components/markets/MarketFilters'
import MarketList from '../components/markets/MarketList'
import PageTitle from '../components/ui/PageTitle'
import { closingWindowOf, type ClosingPreset, type ClosingWindow } from '../utils/closingWindow'

// The preset chosen and the window it meant when it was chosen. Worked out
// once, on the choice, so every page of one list asks for the same window
// even if a midnight passes while the trader pages (#442).
interface ClosingChoice {
  preset: ClosingPreset
  window: ClosingWindow
}

export default function MarketsPage() {
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState<StatusFilter | null>(null)
  const [closing, setClosing] = useState<ClosingChoice | null>(null)

  function handleClosingChange(preset: ClosingPreset | null) {
    setClosing(preset === null ? null : { preset, window: closingWindowOf(preset, new Date()) })
  }

  return (
    <AppLayout width="max-w-[1200px]">
      <PageTitle className="mb-8">Markets</PageTitle>

      <MarketFilters
        query={query}
        status={status}
        onQueryChange={setQuery}
        onStatusChange={setStatus}
        closing={closing?.preset ?? null}
        onClosingChange={handleClosingChange}
      />

      {/* A new key per search, status and closing window, so each is its own list from page one. */}
      <MarketList
        key={`${status ?? 'all'}|${query}|${closing?.window.closesFrom ?? ''}|${closing?.window.closesBefore ?? ''}`}
        query={query}
        status={status}
        closingWindow={closing?.window ?? null}
      />
    </AppLayout>
  )
}
