import { useState } from 'react'
import AppLayout from '../components/layout/AppLayout'
import MarketFilters, { type StatusFilter } from '../components/markets/MarketFilters'
import MarketList from '../components/markets/MarketList'
import PageTitle from '../components/ui/PageTitle'
import type { ClosingPreset } from '../utils/closingWindow'

export default function MarketsPage() {
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState<StatusFilter | null>(null)
  const [closing, setClosing] = useState<ClosingPreset | null>(null)

  return (
    <AppLayout width="max-w-[1200px]">
      <PageTitle className="mb-8">Markets</PageTitle>

      <MarketFilters
        query={query}
        status={status}
        onQueryChange={setQuery}
        onStatusChange={setStatus}
        closing={closing}
        onClosingChange={setClosing}
      />

      {/* A new key per search, status and closing preset, so each is its own
          list from page one, and works its window out as it starts. */}
      <MarketList key={`${status ?? 'all'}|${query}|${closing ?? 'any'}`} query={query} status={status} closing={closing} />
    </AppLayout>
  )
}
