import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { MarketOverviewRow } from '../../api/marketApi'
import { server } from '../../test/server'
import AdminMarketRow from './AdminMarketRow'

const CLOSE = (id: string) => `http://localhost:8001/markets/${id}/close`

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

async function openCloseModal(market: MarketOverviewRow = OPEN_MARKET) {
  renderRow(market)
  const actor = userEvent.setup()
  await actor.click(screen.getByRole('button', { name: /^close$/i }))
  return actor
}

describe('AdminMarketRow', () => {
  it('offers Close on another administrator\'s market, not only the creator\'s', () => {
    // market-service.md's notes for #56, point 7.
    renderRow(OPEN_MARKET, false)
    expect(screen.getByRole('button', { name: /^close$/i })).toBeInTheDocument()
  })

  it('offers Close on the administrator\'s own open market that has not reached its close_time', () => {
    renderRow(OPEN_MARKET, true)
    expect(screen.getByRole('button', { name: /^close$/i })).toBeInTheDocument()
  })

  it('hides the Close control once a market has already stopped trading', () => {
    renderRow({ ...OPEN_MARKET, status: 'closed' })
    expect(screen.queryByRole('button', { name: /^close$/i })).not.toBeInTheDocument()
  })

  it('hides the Close control on a market whose close_time has passed, even while status still reads open', () => {
    // market-service.md's notes for #56, point 1: the same tradeable check as
    // everywhere else, not the raw status column.
    renderRow({ ...OPEN_MARKET, close_time: '2020-01-01T00:00:00Z' })
    expect(screen.queryByRole('button', { name: /^close$/i })).not.toBeInTheDocument()
  })

  it.each([
    { status: 'draft' as const, close_time: null },
    { status: 'submitted' as const, close_time: OPEN_MARKET.close_time },
  ])('hides the Close control on a $status market', ({ status, close_time }) => {
    renderRow({ ...OPEN_MARKET, status, close_time })
    expect(screen.queryByRole('button', { name: /^close$/i })).not.toBeInTheDocument()
  })

  it('disables Confirm Close until the reason reaches 10 trimmed characters', async () => {
    const actor = await openCloseModal()
    const confirm = screen.getByRole('button', { name: /confirm close/i })
    expect(confirm).toBeDisabled()

    await actor.type(screen.getByLabelText(/reason/i), '   too short   ')
    expect(confirm).toBeDisabled()

    await actor.clear(screen.getByLabelText(/reason/i))
    await actor.type(screen.getByLabelText(/reason/i), 'Source retracted its print')
    expect(confirm).toBeEnabled()
  })

  it('closes the market and repaints the row from the response, without a refetch', async () => {
    server.use(
      http.post(CLOSE(OPEN_MARKET.id), () => HttpResponse.json({ status: 'closed', close_time: OPEN_MARKET.close_time, closed_at: '2026-01-01T00:00:00Z' })),
    )
    const actor = await openCloseModal()
    await actor.type(screen.getByLabelText(/reason/i), 'Source retracted its print')

    await actor.click(screen.getByRole('button', { name: /confirm close/i }))

    await screen.findAllByText('Closed')
    expect(screen.getAllByText('Closed')).toHaveLength(2) // the close-time label and the status badge
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^close$/i })).not.toBeInTheDocument()
  })

  it('cancels without closing the market', async () => {
    const actor = await openCloseModal()
    await actor.click(screen.getByRole('button', { name: /cancel/i }))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^close$/i })).toBeInTheDocument()
  })

  it.each([
    { code: 'market_closed', message: /somebody else closed/i },
    { code: 'market_already_settled', message: /been settled/i },
  ])('offers a Reload control and drops Close on a 409 $code', async ({ code, message }) => {
    // market-service.md's notes for #56, point 6, and the close table: the
    // market already stopped some other way — reload rather than retry.
    server.use(
      http.post(CLOSE(OPEN_MARKET.id), () => HttpResponse.json({ error: { code, message: 'stopped' } }, { status: 409 })),
    )
    const actor = await openCloseModal()
    await actor.type(screen.getByLabelText(/reason/i), 'Source retracted its print')

    await actor.click(screen.getByRole('button', { name: /confirm close/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(message)
    expect(screen.getByRole('button', { name: /reload/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^close$/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('shows the generic failure and keeps Close available for a retry when the server errors', async () => {
    server.use(
      http.post(CLOSE(OPEN_MARKET.id), () => HttpResponse.json({ error: { code: 'internal_error', message: 'boom' } }, { status: 500 })),
    )
    const actor = await openCloseModal()
    await actor.type(screen.getByLabelText(/reason/i), 'Source retracted its print')

    await actor.click(screen.getByRole('button', { name: /confirm close/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Closing this market failed. Please try again.')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /reload/i })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^close$/i })).toBeInTheDocument()
  })

  it('paints a too-short reason on the field, the same shape as blocking_submission, and keeps the modal open', async () => {
    // market-service.md's notes for #56, point 3. The client-side 10-character
    // gate makes this unreachable in the ordinary flow; this is the server
    // disagreeing with the client, e.g. after a trim-rule change.
    server.use(
      http.post(CLOSE(OPEN_MARKET.id), () => HttpResponse.json(
        { error: { code: 'close_incomplete', message: 'bad reason', details: [{ field: 'reason', message: 'The reason must be at least 10 characters.' }] } },
        { status: 422 },
      )),
    )
    const actor = await openCloseModal()
    await actor.type(screen.getByLabelText(/reason/i), '1234567890')

    await actor.click(screen.getByRole('button', { name: /confirm close/i }))

    expect(await screen.findByText(/at least 10 characters/i)).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })
})
