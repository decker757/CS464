import { act, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { AdminAction } from '../../api/auditApi'
import type { MarketOverviewRow } from '../../api/marketApi'
import { server } from '../../test/server'
import AdminMarketRow from './AdminMarketRow'

const CLOSE = (id: string) => `http://localhost:8001/markets/${id}/close`
const SETTLE = (id: string) => `http://localhost:8003/ledger/markets/${id}/settlement`
const SEND_BACK = (id: string) => `http://localhost:8001/markets/${id}/send-back-outcome`

const OPEN_MARKET: MarketOverviewRow = {
  id: 'm1',
  creator_id: 'u1',
  status: 'open',
  question: 'Will SMU win SUNIG?',
  close_time: '2099-01-05T12:00:00Z',
  settleable: false,
  dispute_window_ends_at: null,
}

const SETTLEABLE_MARKET: MarketOverviewRow = {
  id: 'm2',
  creator_id: 'u1',
  status: 'approved',
  question: 'Did it rain yesterday?',
  close_time: '2025-01-01T00:00:00Z',
  settleable: true,
  dispute_window_ends_at: '2025-01-01T00:05:00Z',
}

function renderRow(market: MarketOverviewRow, isMine = false, approvalEntry?: AdminAction) {
  return render(
    <MemoryRouter>
      <AdminMarketRow market={market} isMine={isMine} approvalEntry={approvalEntry} />
    </MemoryRouter>,
  )
}

async function openCloseModal(market: MarketOverviewRow = OPEN_MARKET) {
  renderRow(market)
  const actor = userEvent.setup()
  await actor.click(screen.getByRole('button', { name: /^close$/i }))
  return actor
}

// Settle has no reason to collect, so confirming it is Settle then Confirm
// Settle, rather than Settle filling a field before Confirm Close does.
async function settle(market: MarketOverviewRow = SETTLEABLE_MARKET) {
  renderRow(market)
  const actor = userEvent.setup()
  await actor.click(screen.getByRole('button', { name: /^settle$/i }))
  await actor.click(screen.getByRole('button', { name: /^confirm settle$/i }))
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

  it('links to the exposure panel for a market that has traded, but not a draft or submitted one', () => {
    renderRow(OPEN_MARKET)
    expect(screen.getByRole('link', { name: /^exposure$/i })).toHaveAttribute('href', `/admin/markets/${OPEN_MARKET.id}/exposure`)
  })

  it('links to the price history on a published market, whether or not it has traded', () => {
    renderRow(OPEN_MARKET)
    expect(screen.getByRole('link', { name: /price history/i })).toHaveAttribute('href', `/admin/markets/${OPEN_MARKET.id}/price-history`)
  })

  // Both reads sit behind one condition in the row, so one test covers both.
  it('hides the exposure and price history links on a draft or submitted market', () => {
    renderRow({ ...OPEN_MARKET, status: 'submitted', close_time: '2099-01-01T00:00:00Z' })
    expect(screen.queryByRole('link', { name: /^exposure$/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /price history/i })).not.toBeInTheDocument()
  })

  describe('settling', () => {
    it('hides the Settle control on an approved market still in its dispute window', () => {
      renderRow({ ...SETTLEABLE_MARKET, settleable: false })
      expect(screen.queryByRole('button', { name: /^settle$/i })).not.toBeInTheDocument()
    })

    it('offers Settle once a market is settleable', () => {
      renderRow(SETTLEABLE_MARKET)
      expect(screen.getByRole('button', { name: /^settle$/i })).toBeInTheDocument()
    })

    it('asks for confirmation before settling, and does not call the server until confirmed', async () => {
      // Settling pays out every winning holder and cannot be undone, unlike
      // the draft autosave this repo never confirms — a payout earns a step.
      let calls = 0
      server.use(http.post(SETTLE(SETTLEABLE_MARKET.id), () => { calls += 1; return HttpResponse.json({}) }))
      renderRow(SETTLEABLE_MARKET)
      const actor = userEvent.setup()

      await actor.click(screen.getByRole('button', { name: /^settle$/i }))

      expect(screen.getByRole('dialog', { name: /settle market/i })).toBeInTheDocument()
      expect(calls).toBe(0)
    })

    it('cancels without settling the market', async () => {
      renderRow(SETTLEABLE_MARKET)
      const actor = userEvent.setup()
      await actor.click(screen.getByRole('button', { name: /^settle$/i }))

      await actor.click(screen.getByRole('button', { name: /cancel/i }))

      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: /^settle$/i })).toBeInTheDocument()
    })

    it('settles the market and repaints the row as settled, without a refetch', async () => {
      server.use(http.post(SETTLE(SETTLEABLE_MARKET.id), () => HttpResponse.json({
        market_id: SETTLEABLE_MARKET.id, outcome_id: 'o1', recorded_at: '2026-01-01T00:00:00Z',
        holders_paid: 3, total_paid: '140.0000', residue: '0.0000',
      })))

      await settle()

      await screen.findByText('Settled')
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /^settle$/i })).not.toBeInTheDocument()
    })

    it('sends the settlement request with an empty body and nothing else', async () => {
      // The ledger answers 422 invalid_request to any field, so a stray body
      // would break settlement completely.
      let body = ''
      server.use(http.post(SETTLE(SETTLEABLE_MARKET.id), async ({ request }) => {
        body = await request.text()
        return HttpResponse.json({
          market_id: SETTLEABLE_MARKET.id, outcome_id: 'o1', recorded_at: '2026-01-01T00:00:00Z',
          holders_paid: 3, total_paid: '140.0000', residue: '0.0000',
        })
      }))

      await settle()

      await screen.findByText('Settled')
      expect(body).toBe('')
    })

    it('clears the stale error once a retry after dispute_window_open succeeds', async () => {
      let calls = 0
      server.use(http.post(SETTLE(SETTLEABLE_MARKET.id), () => {
        calls += 1
        if (calls === 1) {
          return HttpResponse.json({ error: { code: 'dispute_window_open', message: 'not yet' } }, { status: 409 })
        }
        return HttpResponse.json({
          market_id: SETTLEABLE_MARKET.id, outcome_id: 'o1', recorded_at: '2026-01-01T00:00:00Z',
          holders_paid: 3, total_paid: '140.0000', residue: '0.0000',
        })
      }))

      const actor = await settle()
      await screen.findByRole('alert')

      await actor.click(screen.getByRole('button', { name: /^settle$/i }))
      await actor.click(screen.getByRole('button', { name: /^confirm settle$/i }))

      await screen.findByText('Settled')
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    })

    it('shows that nothing was paid, offers Reload, and keeps Settle available when the dispute window is still open at the edge', async () => {
      // ledger-service.md: settleable can read true here and still race the
      // window's edge, since the row's settleable was read on an earlier request.
      server.use(http.post(SETTLE(SETTLEABLE_MARKET.id), () => HttpResponse.json(
        { error: { code: 'dispute_window_open', message: 'not yet' } }, { status: 409 },
      )))

      await settle()

      expect(await screen.findByRole('alert')).toHaveTextContent(/dispute window has not closed/i)
      expect(screen.queryByRole('alert')).not.toHaveTextContent(/failed/i)
      expect(screen.getByRole('button', { name: /reload/i })).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /^settle$/i })).toBeEnabled()
    })

    it('keeps Settle available for a retry when market_service could not be reached', async () => {
      // ledger-service.md: nothing was paid when the book read fails, unlike
      // settlement_unconfirmed below, where the payouts already committed.
      server.use(http.post(SETTLE(SETTLEABLE_MARKET.id), () => HttpResponse.json(
        { error: { code: 'market_terms_unavailable', message: 'unreachable' } }, { status: 503 },
      )))

      await settle()

      expect(await screen.findByRole('alert')).toHaveTextContent(/nothing was paid/i)
      expect(screen.getByRole('button', { name: /^settle$/i })).toBeEnabled()
    })

    it('offers a Reload control and drops Settle once the market is no longer approved', async () => {
      server.use(http.post(SETTLE(SETTLEABLE_MARKET.id), () => HttpResponse.json(
        { error: { code: 'market_not_approved', message: 'not approved' } }, { status: 409 },
      )))

      await settle()

      expect(await screen.findByRole('alert')).toHaveTextContent(/no longer approved/i)
      expect(screen.getByRole('button', { name: /reload/i })).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /^settle$/i })).not.toBeInTheDocument()
    })

    it('retries on settlement_unconfirmed and settles silently once the retry confirms it', async () => {
      // ledger-service.md: the payouts already committed; only the last step
      // (telling market_service) failed. This must read as success, not failure.
      let calls = 0
      server.use(http.post(SETTLE(SETTLEABLE_MARKET.id), () => {
        calls += 1
        if (calls === 1) {
          return HttpResponse.json({ error: { code: 'settlement_unconfirmed', message: 'unconfirmed' } }, { status: 503 })
        }
        return HttpResponse.json({
          market_id: SETTLEABLE_MARKET.id, outcome_id: 'o1', recorded_at: '2026-01-01T00:00:00Z',
          holders_paid: 3, total_paid: '140.0000', residue: '0.0000',
        })
      }))

      await settle()

      await screen.findByText('Settled')
      expect(screen.queryByRole('alert')).not.toBeInTheDocument()
      expect(calls).toBe(2)
    })

    it('tells the admin payouts are confirmed, not failed, if the retry also fails to confirm', async () => {
      server.use(http.post(SETTLE(SETTLEABLE_MARKET.id), () => HttpResponse.json(
        { error: { code: 'settlement_unconfirmed', message: 'unconfirmed' } }, { status: 503 },
      )))

      await settle()

      expect(await screen.findByRole('alert')).toHaveTextContent(/settlement is recorded/i)
      expect(screen.queryByRole('alert')).not.toHaveTextContent(/^settling this market failed/i)
    })

    it('says the settlement is recorded, never that nothing was paid, when the retry itself cannot reach market_service', async () => {
      // settlement_unconfirmed means the payouts already committed. A retry
      // that comes back market_terms_unavailable is the realistic case —
      // ledger-service.md has the ledger check market_service first on a
      // repeat, so a market that has been paid is never refused for its
      // status — and CODE_MESSAGES's "nothing was paid" wording for that code
      // would be false here. Settle must also stay available: a confirmed
      // payout nobody can finish telling market_service about must not be
      // stranded behind a hidden control.
      let calls = 0
      server.use(http.post(SETTLE(SETTLEABLE_MARKET.id), () => {
        calls += 1
        if (calls === 1) {
          return HttpResponse.json({ error: { code: 'settlement_unconfirmed', message: 'unconfirmed' } }, { status: 503 })
        }
        return HttpResponse.json({ error: { code: 'market_terms_unavailable', message: 'unreachable' } }, { status: 503 })
      }))

      await settle()

      expect(await screen.findByRole('alert')).toHaveTextContent(/settlement is recorded/i)
      expect(screen.queryByRole('alert')).not.toHaveTextContent(/nothing was paid/i)
      expect(screen.getByRole('button', { name: /^settle$/i })).toBeEnabled()
      expect(calls).toBe(2)
    })
  })

  describe('disputing the result', () => {
    const DISPUTABLE_MARKET: MarketOverviewRow = {
      id: 'm3',
      creator_id: 'u1',
      status: 'approved',
      question: 'Did it rain yesterday?',
      close_time: '2025-01-01T00:00:00Z',
      settleable: false,
      dispute_window_ends_at: '2099-01-01T00:05:00Z',
    }

    function approvedEntry(overrides: Partial<AdminAction> = {}): AdminAction {
      return {
        id: 'a1',
        occurred_at: '2026-01-01T00:00:00Z',
        actor_id: 'admin-2',
        actor_username: 'ihsan_b',
        actor_role: 'admin',
        action_type: 'market.outcome_approved',
        target_type: 'market',
        target_id: DISPUTABLE_MARKET.id,
        target_label: DISPUTABLE_MARKET.question ?? '',
        reason: null,
        context: {
          proposal_id: 'prop-1',
          winning_outcome: 'Yes',
          evidence_url: 'https://example.com',
          evidence_note: 'A note.',
          proposed_by_username: 'ernest_t',
        },
        source_service: 'market_service',
        ...overrides,
      }
    }

    async function openDisputeModal(market: MarketOverviewRow = DISPUTABLE_MARKET, entry = approvedEntry()) {
      renderRow(market, false, entry)
      const actor = userEvent.setup()
      await actor.click(screen.getByRole('button', { name: /dispute result/i }))
      return actor
    }

    it('shows time left while the dispute window is open', () => {
      renderRow(DISPUTABLE_MARKET, false, approvedEntry())
      expect(screen.getByText(/disputes close in/i)).toBeInTheDocument()
    })

    it('shows the result is final once the dispute window has passed, with the closed instant so a wrong clock is visible', () => {
      renderRow({ ...DISPUTABLE_MARKET, dispute_window_ends_at: '2020-01-01T00:00:00Z' }, false, approvedEntry())
      expect(screen.getByText(/result is final; payout pending \(closed .+\)\./i)).toBeInTheDocument()
    })

    it('shows no countdown and offers no Dispute result when the market is not approved, even with a live window and an approval entry', () => {
      // Everything else is what an approved row has, so the status is the
      // only thing that can be hiding the countdown and the button.
      renderRow({ ...DISPUTABLE_MARKET, status: 'closed' }, false, approvedEntry())
      expect(screen.queryByText(/disputes close in/i)).not.toBeInTheDocument()
      expect(screen.queryByText(/result is final/i)).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /dispute result/i })).not.toBeInTheDocument()
    })

    it('says no window is recorded, and promises no payout, on an approved market with no stored window', () => {
      // ADR 0023, section 5: a null is damage and nothing will be paid.
      renderRow({ ...DISPUTABLE_MARKET, dispute_window_ends_at: null }, false, approvedEntry())
      expect(screen.getByText(/no dispute window is recorded/i)).toBeInTheDocument()
      expect(screen.queryByText(/payout pending/i)).not.toBeInTheDocument()
    })

    it('moves the minutes on while the page is left open, not only the flip to "final"', () => {
      vi.useFakeTimers()
      try {
        const endsAt = new Date(Date.now() + 2 * 60 * 60 * 1000 + 30 * 1000).toISOString()
        renderRow({ ...DISPUTABLE_MARKET, dispute_window_ends_at: endsAt }, false, approvedEntry())
        expect(screen.getByText(/disputes close in 2h 0m/i)).toBeInTheDocument()

        act(() => { vi.advanceTimersByTime(60 * 60 * 1000) })

        expect(screen.getByText(/disputes close in 1h 0m/i)).toBeInTheDocument()
      } finally {
        vi.useRealTimers()
      }
    })

    it('turns into "Result is final" and drops Dispute result on its own, with the page left open', () => {
      // Without a timer, nothing re-renders an approved market with no
      // trades, so a stale "Disputes close in 10m" could sit on screen for
      // hours after the window actually closed.
      vi.useFakeTimers()
      try {
        const endsAt = new Date(Date.now() + 1000).toISOString()
        renderRow({ ...DISPUTABLE_MARKET, dispute_window_ends_at: endsAt }, false, approvedEntry())
        expect(screen.getByText(/disputes close in/i)).toBeInTheDocument()
        expect(screen.getByRole('button', { name: /dispute result/i })).toBeInTheDocument()

        act(() => { vi.advanceTimersByTime(1001) })

        expect(screen.getByText(/result is final; payout pending \(closed .+\)\./i)).toBeInTheDocument()
        expect(screen.queryByRole('button', { name: /dispute result/i })).not.toBeInTheDocument()
      } finally {
        vi.useRealTimers()
      }
    })

    it('hides Dispute result on an approved market still within its window while the approval entry has not loaded yet', () => {
      // AdminMarketsPage fetches the approval entry separately from the
      // market list; before it answers, approvalEntry is undefined even on a
      // genuinely disputable row. The button must not render clickable with
      // nothing behind it to send.
      renderRow(DISPUTABLE_MARKET, false, undefined)
      expect(screen.queryByRole('button', { name: /dispute result/i })).not.toBeInTheDocument()
    })

    it('hides Dispute result on an approved market with no dispute window left', () => {
      renderRow({ ...DISPUTABLE_MARKET, dispute_window_ends_at: '2020-01-01T00:00:00Z' }, false, approvedEntry())
      expect(screen.queryByRole('button', { name: /dispute result/i })).not.toBeInTheDocument()
    })

    it('offers Dispute result to any administrator, including the creator (always the proposer)', () => {
      // market-service.md's dispute-screen notes, point 2: never disabled for
      // them. The proposer is always the creator, so isMine covers that half;
      // the row has no way to know who approved, so the approver half is
      // covered by the end-to-end dispute journey instead.
      renderRow(DISPUTABLE_MARKET, true, approvedEntry())
      expect(screen.getByRole('button', { name: /dispute result/i })).toBeEnabled()
    })

    it('shows what was approved: the winner, the evidence, the proposer and the approver', async () => {
      // market-service.md's dispute-screen notes, point 3: an admin
      // disputing one of several approved rows must see what they are
      // overturning, not just the question and a countdown.
      await openDisputeModal(DISPUTABLE_MARKET, approvedEntry())

      const dialog = screen.getByRole('dialog', { name: /dispute result/i })
      expect(dialog).toHaveTextContent('Yes')
      expect(dialog).toHaveTextContent('A note.')
      expect(screen.getByRole('link', { name: 'https://example.com' })).toBeInTheDocument()
      expect(dialog).toHaveTextContent('ernest_t')
      expect(dialog).toHaveTextContent('ihsan_b')
    })

    it('disables Confirm Dispute until the reason reaches 10 trimmed characters', async () => {
      const actor = await openDisputeModal()
      const confirm = screen.getByRole('button', { name: /confirm dispute/i })
      expect(confirm).toBeDisabled()

      await actor.type(screen.getByLabelText(/reason/i), 'too short')
      expect(confirm).toBeDisabled()

      await actor.clear(screen.getByLabelText(/reason/i))
      await actor.type(screen.getByLabelText(/reason/i), 'The source was revised after publication')
      expect(confirm).toBeEnabled()
    })

    it('sends the proposal_id read from the approval entry, never refetched', async () => {
      // market-service.md's dispute-screen notes, point 4.
      let sentProposalId: unknown
      server.use(
        http.post(SEND_BACK(DISPUTABLE_MARKET.id), async ({ request }) => {
          const body = await request.json() as { proposal_id: unknown }
          sentProposalId = body.proposal_id
          return HttpResponse.json({ status: 'closed', closed_at: DISPUTABLE_MARKET.close_time })
        }),
      )
      const actor = await openDisputeModal(DISPUTABLE_MARKET, approvedEntry({ context: { proposal_id: 'prop-xyz' } }))
      await actor.type(screen.getByLabelText(/reason/i), 'The source was revised after publication')

      await actor.click(screen.getByRole('button', { name: /confirm dispute/i }))

      await screen.findAllByText('Closed')
      expect(sentProposalId).toBe('prop-xyz')
    })

    it('sends back the result and repaints the row as closed, without a refetch', async () => {
      server.use(
        http.post(SEND_BACK(DISPUTABLE_MARKET.id), () => HttpResponse.json({ status: 'closed', closed_at: DISPUTABLE_MARKET.close_time })),
      )
      const actor = await openDisputeModal()
      await actor.type(screen.getByLabelText(/reason/i), 'The source was revised after publication')

      await actor.click(screen.getByRole('button', { name: /confirm dispute/i }))

      await screen.findAllByText('Closed')
      expect(screen.getAllByText('Closed')).toHaveLength(2) // the close-time label and the status badge
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /dispute result/i })).not.toBeInTheDocument()
    })

    it('cancels without sending the result back', async () => {
      const actor = await openDisputeModal()
      await actor.click(screen.getByRole('button', { name: /cancel/i }))

      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: /dispute result/i })).toBeInTheDocument()
    })

    it.each([
      { code: 'dispute_window_closed', message: /dispute window has closed/i },
      { code: 'proposal_superseded', message: /already replaced/i },
      { code: 'market_already_settled', message: /acted on this market first/i },
    ])('offers a Reload control and drops Dispute result on a 409 $code', async ({ code, message }) => {
      server.use(
        http.post(SEND_BACK(DISPUTABLE_MARKET.id), () => HttpResponse.json({ error: { code, message: 'stopped' } }, { status: 409 })),
      )
      const actor = await openDisputeModal()
      await actor.type(screen.getByLabelText(/reason/i), 'The source was revised after publication')

      await actor.click(screen.getByRole('button', { name: /confirm dispute/i }))

      expect(await screen.findByRole('alert')).toHaveTextContent(message)
      expect(screen.getByRole('button', { name: /reload/i })).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /dispute result/i })).not.toBeInTheDocument()
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    })

    it('paints a too-short reason on the field and keeps the modal open', async () => {
      server.use(
        http.post(SEND_BACK(DISPUTABLE_MARKET.id), () => HttpResponse.json(
          { error: { code: 'send_back_incomplete', message: 'bad reason', details: [{ field: 'reason', message: 'The reason must be at least 10 characters.' }] } },
          { status: 422 },
        )),
      )
      const actor = await openDisputeModal()
      await actor.type(screen.getByLabelText(/reason/i), '1234567890')

      await actor.click(screen.getByRole('button', { name: /confirm dispute/i }))

      expect(await screen.findByText(/at least 10 characters/i)).toBeInTheDocument()
      expect(screen.getByRole('dialog')).toBeInTheDocument()
    })

    it('shows the generic failure and keeps Dispute result available for a retry when the server errors', async () => {
      server.use(
        http.post(SEND_BACK(DISPUTABLE_MARKET.id), () => HttpResponse.json({ error: { code: 'internal_error', message: 'boom' } }, { status: 500 })),
      )
      const actor = await openDisputeModal()
      await actor.type(screen.getByLabelText(/reason/i), 'The source was revised after publication')

      await actor.click(screen.getByRole('button', { name: /confirm dispute/i }))

      expect(await screen.findByRole('alert')).toHaveTextContent('Sending this result back failed. Please try again.')
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
      expect(screen.getByRole('button', { name: /dispute result/i })).toBeInTheDocument()
    })
  })
})
