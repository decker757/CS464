import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { MarketOverviewRow } from '../../api/marketApi'
import AdminMarketRow from './AdminMarketRow'

const OPEN_MARKET: MarketOverviewRow = {
  id: 'm1',
  creator_id: 'u1',
  status: 'open',
  question: 'Will SMU win SUNIG?',
  close_time: '2099-01-05T12:00:00Z',
}

function renderRow(market: MarketOverviewRow, isMine = false) {
  return render(
    <MemoryRouter>
      <AdminMarketRow market={market} isMine={isMine} />
    </MemoryRouter>,
  )
}

describe('AdminMarketRow', () => {
  it('links to price history for a market that has traded, but not a draft or submitted one', () => {
    renderRow(OPEN_MARKET)
    expect(screen.getByRole('link', { name: /price history/i })).toHaveAttribute('href', `/admin/markets/${OPEN_MARKET.id}/price-history`)
  })

  it('hides the price history link on a draft or submitted market', () => {
    renderRow({ ...OPEN_MARKET, status: 'draft', close_time: null })
    expect(screen.queryByRole('link', { name: /price history/i })).not.toBeInTheDocument()
  })

  it('offers Propose Outcome only to the creator of a closed market', () => {
    renderRow({ ...OPEN_MARKET, status: 'closed' }, true)
    expect(screen.getByRole('link', { name: /propose outcome/i })).toBeInTheDocument()
  })

  it('hides Propose Outcome from another administrator', () => {
    renderRow({ ...OPEN_MARKET, status: 'closed' }, false)
    expect(screen.queryByRole('link', { name: /propose outcome/i })).not.toBeInTheDocument()
  })
})
