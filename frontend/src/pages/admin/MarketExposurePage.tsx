import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getMarketExposure, type MarketExposure } from '../../api/ledgerApi'
import { getMarket, type PublicMarketDetail } from '../../api/marketApi'
import AppLayout from '../../components/layout/AppLayout'
import MarketExposureTable from '../../components/markets/MarketExposureTable'
import BackLink from '../../components/ui/BackLink'
import DetailItem from '../../components/ui/DetailItem'
import PageTitle from '../../components/ui/PageTitle'
import SectionCard from '../../components/ui/SectionCard'
import { formatCreditsPrecise } from '../../utils/formatCredits'

// [2.2] #55. The backend slice (#6) has not shipped yet, so getMarketExposure
// in ledgerApi.ts is inferred from #6's own acceptance criteria — not read
// off docs/api/, which has nothing for it yet. See ledgerApi.ts for what is
// assumed and reconcile it once #6 lands.
//
// Not scoped to the market's creator: knowing payout size before resolving
// is #6's whole story, which is oversight, not authorship — same reasoning
// as #8's price history and DecideOutcomePage, so this reads the public
// detail, not getMyMarket.
export default function MarketExposurePage() {
  const { id } = useParams<{ id: string }>()

  const [market, setMarket] = useState<PublicMarketDetail | null>(null)
  const [marketError, setMarketError] = useState(false)

  const [exposure, setExposure] = useState<MarketExposure | null>(null)
  const [exposureLoading, setExposureLoading] = useState(true)
  const [exposureError, setExposureError] = useState(false)

  useEffect(() => {
    if (!id) return
    let ignored = false
    getMarket(id)
      .then(found => { if (!ignored) setMarket(found) })
      .catch(() => { if (!ignored) setMarketError(true) })
    return () => { ignored = true }
  }, [id])

  useEffect(() => {
    if (!id) return
    let ignored = false
    getMarketExposure(id)
      .then(found => { if (!ignored) setExposure(found) })
      .catch(() => { if (!ignored) setExposureError(true) })
      .finally(() => { if (!ignored) setExposureLoading(false) })
    return () => { ignored = true }
  }, [id])

  const outcomeLabels = new Map((market?.outcomes ?? []).map(outcome => [outcome.id, outcome.label]))

  return (
    <AppLayout width="max-w-[900px]">
      <BackLink to="/admin/markets" label="All Markets" className="mb-4 block" />

      {marketError && <p role="alert" className="text-sm text-danger">Failed to load this market.</p>}

      {market && (
        <>
          <PageTitle className="mb-8">{market.question}</PageTitle>

          {exposureLoading && <p className="text-sm text-muted">Loading exposure…</p>}
          {exposureError && <p role="alert" className="text-sm text-danger">Failed to load exposure.</p>}

          {!exposureLoading && !exposureError && exposure && (
            <>
              <SectionCard title="Pool" className="mb-6">
                <DetailItem label="Seed subsidy plus everything traders have paid in">
                  <p className="text-smu-navy">{exposure.pool === null ? '—' : formatCreditsPrecise(exposure.pool)}</p>
                </DetailItem>
                {exposure.exceeds_pool && (
                  <p role="alert" className="mt-4 text-sm font-semibold text-danger">
                    At least one outcome's max payout exceeds the pool.
                  </p>
                )}
              </SectionCard>

              {exposure.outcomes.length === 0 ? (
                <p className="text-sm text-muted">No trades yet, so there is no exposure to show.</p>
              ) : (
                <MarketExposureTable outcomes={exposure.outcomes} outcomeLabels={outcomeLabels} />
              )}
            </>
          )}
        </>
      )}
    </AppLayout>
  )
}
