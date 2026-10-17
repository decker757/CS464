import { useState } from 'react'
import AppLayout from '../components/layout/AppLayout'
import MarketFilters, { type StatusFilter } from '../components/markets/MarketFilters'
import MarketList from '../components/markets/MarketList'
import PageTitle from '../components/ui/PageTitle'

export default function MarketsPage() {
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState<StatusFilter | null>(null)

  return (
    <AppLayout width="max-w-[1200px]">
      <PageTitle className="mb-8">Markets</PageTitle>

      <MarketFilters query={query} status={status} onQueryChange={setQuery} onStatusChange={setStatus} />

      {/* A new key per search and status, so each is its own list from page one. */}
      <MarketList key={`${status ?? 'all'}|${query}`} query={query} status={status} />
    </AppLayout>
  )
}
