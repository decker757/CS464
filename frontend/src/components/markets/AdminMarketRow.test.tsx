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
  it('links to the exposure panel for a market that has traded, but not a draft or submitted one', () => {
    renderRow(OPEN_MARKET)
    expect(screen.getByRole('link', { name: /^exposure$/i })).toHaveAttribute('href', `/admin/markets/${OPEN_MARKET.id}/exposure`)
  })

  it('hides the exposure link on a draft or submitted market', () => {
    renderRow({ ...OPEN_MARKET, status: 'submitted', close_time: '2099-01-01T00:00:00Z' })
    expect(screen.queryByRole('link', { name: /^exposure$/i })).not.toBeInTheDocument()
  })
})
