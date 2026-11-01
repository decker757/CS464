import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import AppNavbar from '../components/AppNavbar'
import { server } from '../test/server'
import { AuthContext, type User } from './AuthContext'
import { BalanceProvider, useBalance } from './BalanceContext'

const LEDGER_BASE = 'http://localhost:8003'

const alice: User = { id: '1', username: 'alice', email: 'alice@smu.edu.sg', role: 'trader', created_at: '2026-01-01' }
const bob: User = { id: '2', username: 'bob', email: 'bob@smu.edu.sg', role: 'trader', created_at: '2026-01-01' }

function describeBalance(balance: string | null | undefined): string {
  if (balance === undefined) return 'no balance'
  if (balance === null) return 'failed'
  return balance
}

function DisplayBalance() {
  const { balance, refetch } = useBalance()
  return (
    <>
      <p>{describeBalance(balance)}</p>
      <button onClick={() => refetch()}>Refetch</button>
    </>
  )
}

/** The balance route answering for `user` the way the ledger does for an
 * account with no resting orders, so `available` equals `balance`, and
 * counting each request. */
function mockBalance(user: User, balance: string): { requestCount: () => number } {
  let count = 0
  server.use(
    http.get(`${LEDGER_BASE}/ledger/balances/me`, () => {
      count += 1
      return HttpResponse.json({
        user_id: user.id, account_id: 'acc-1',
        balance, reserved: '0.0000', available: balance,
      })
    }),
  )
  return { requestCount: () => count }
}

function renderWithUser(user: User | null | undefined) {
  return render(
    <AuthContext.Provider value={{ user, login: () => {}, logout: async () => {} }}>
      <BalanceProvider>
        <DisplayBalance />
      </BalanceProvider>
    </AuthContext.Provider>,
  )
}

/** The real header under the real provider: the figure a trader watches for
 * a payout is the navbar's, so that is what these tests read. */
function headerFor(user: User | null) {
  return (
    <AuthContext.Provider value={{ user, login: () => {}, logout: async () => {} }}>
      <BalanceProvider>
        <MemoryRouter>
          <AppNavbar />
        </MemoryRouter>
      </BalanceProvider>
    </AuthContext.Provider>
  )
}

/** The browser reporting the tab as shown or hidden, then announcing it. */
function changeVisibility(state: DocumentVisibilityState) {
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue(state)
  fireEvent(document, new Event('visibilitychange'))
}

describe('BalanceContext', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('fetches the balance when a user is signed in', async () => {
    mockBalance(alice, '1000.0000')
    renderWithUser(alice)
    expect(await screen.findByText('1000.0000')).toBeInTheDocument()
  })

  it('shows no balance when nobody is signed in, and never fetches', async () => {
    let called = false
    server.use(
      http.get(`${LEDGER_BASE}/ledger/balances/me`, () => {
        called = true
        return HttpResponse.json({ user_id: 'x', account_id: 'y', balance: '1000.0000' })
      }),
    )
    renderWithUser(null)
    expect(screen.getByText('no balance')).toBeInTheDocument()
    // Give a pending fetch a chance to land before asserting it never did.
    await new Promise(resolve => setTimeout(resolve, 20))
    expect(called).toBe(false)
  })

  it('keeps the last known balance when a refetch fails', async () => {
    mockBalance(alice, '1000.0000')
    const actor = userEvent.setup()
    renderWithUser(alice)
    await screen.findByText('1000.0000')

    server.use(http.get(`${LEDGER_BASE}/ledger/balances/me`, () => HttpResponse.error()))
    await actor.click(screen.getByRole('button', { name: /refetch/i }))

    // A rejected trade must never look like it changed the balance.
    await new Promise(resolve => setTimeout(resolve, 20))
    expect(screen.getByText('1000.0000')).toBeInTheDocument()
  })

  it('shows failed when the very first load fails, with nothing to fall back to', async () => {
    server.use(http.get(`${LEDGER_BASE}/ledger/balances/me`, () => HttpResponse.error()))
    renderWithUser(alice)
    expect(await screen.findByText('failed')).toBeInTheDocument()
  })

  it('does not let a slow, older request overwrite a newer one that already answered', async () => {
    // Stand-in for a trade: refetch() is called twice in quick succession
    // (the initial mount's, then a trade's), and the first one's reply is
    // slow enough to land after the second's.
    let resolveFirst!: (value: Response) => void
    let callCount = 0
    server.use(
      http.get(`${LEDGER_BASE}/ledger/balances/me`, () => {
        callCount += 1
        if (callCount === 1) {
          return new Promise<Response>(resolve => { resolveFirst = resolve })
        }
        return HttpResponse.json({ user_id: alice.id, account_id: 'acc-1', balance: '995.0000' })
      }),
    )
    const actor = userEvent.setup()
    renderWithUser(alice)
    await screen.findByText('no balance')

    await actor.click(screen.getByRole('button', { name: /refetch/i }))
    await waitFor(() => expect(screen.getByText('995.0000')).toBeInTheDocument())

    // The slow first reply (the stale pre-trade balance) arrives last; it
    // must not replace the newer one already on screen.
    resolveFirst(HttpResponse.json({ user_id: alice.id, account_id: 'acc-1', balance: '1000.0000' }))
    await new Promise(resolve => setTimeout(resolve, 20))
    expect(screen.getByText('995.0000')).toBeInTheDocument()
  })

  it('does not show one user\'s balance for another user signed in on the same session', async () => {
    mockBalance(alice, '1000.0000')
    const { rerender } = render(
      <AuthContext.Provider value={{ user: alice, login: () => {}, logout: async () => {} }}>
        <BalanceProvider>
          <DisplayBalance />
        </BalanceProvider>
      </AuthContext.Provider>,
    )
    await screen.findByText('1000.0000')

    mockBalance(bob, '500.0000')
    rerender(
      <AuthContext.Provider value={{ user: bob, login: () => {}, logout: async () => {} }}>
        <BalanceProvider>
          <DisplayBalance />
        </BalanceProvider>
      </AuthContext.Provider>,
    )

    // Never alice's 1000 at any point during the switch, even before bob's fetch lands.
    expect(screen.queryByText('1000.0000')).not.toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('500.0000')).toBeInTheDocument())
  })

  // [B-2] #511. A payout is written by the administrator's request, and
  // nothing on the socket tells the trader's tab: coming back to the tab is
  // the only moment the header can learn of it without a reload.
  it('re-reads the balance when the tab becomes visible again, so a payout reaches the header', async () => {
    mockBalance(alice, '1000.0000')
    render(headerFor(alice))
    expect(await screen.findByLabelText('available balance')).toHaveTextContent('1,000 credits')

    // Settled while the trader looked at another tab.
    mockBalance(alice, '1250.0000')
    changeVisibility('visible')

    await waitFor(() =>
      expect(screen.getByLabelText('available balance')).toHaveTextContent('1,250 credits'),
    )
  })

  // Clicking from the admin's browser window into the trader's fires `focus`
  // and, with both windows on screen, often no `visibilitychange` at all.
  it('re-reads the balance when the window regains focus', async () => {
    mockBalance(alice, '1000.0000')
    render(headerFor(alice))
    expect(await screen.findByLabelText('available balance')).toHaveTextContent('1,000 credits')

    mockBalance(alice, '1250.0000')
    fireEvent.focus(window)

    await waitFor(() =>
      expect(screen.getByLabelText('available balance')).toHaveTextContent('1,250 credits'),
    )
  })

  // The ledger failing as the trader comes back must not blank or zero the
  // header: the re-read goes through `refetch`, which keeps the last figure.
  it('keeps the last known balance when a re-read on return fails', async () => {
    mockBalance(alice, '1000.0000')
    render(headerFor(alice))
    expect(await screen.findByLabelText('available balance')).toHaveTextContent('1,000 credits')

    let failedCount = 0
    server.use(
      http.get(`${LEDGER_BASE}/ledger/balances/me`, () => {
        failedCount += 1
        return HttpResponse.json({ detail: 'internal_error' }, { status: 500 })
      }),
    )
    fireEvent.focus(window)

    await waitFor(() => expect(failedCount).toBe(1))
    // Give the failed answer a chance to land before asserting what it left.
    await new Promise(resolve => setTimeout(resolve, 20))
    expect(screen.getByLabelText('available balance')).toHaveTextContent('1,000 credits')
  })

  it('does not re-read when the tab is hidden', async () => {
    mockBalance(alice, '1000.0000')
    render(headerFor(alice))
    expect(await screen.findByLabelText('available balance')).toHaveTextContent('1,000 credits')

    const ledger = mockBalance(alice, '1250.0000')
    changeVisibility('hidden')

    // Give a request a chance to land before asserting none was sent.
    await new Promise(resolve => setTimeout(resolve, 20))
    expect(ledger.requestCount()).toBe(0)
  })

  it('requests nothing when the tab comes back with nobody signed in', async () => {
    const ledger = mockBalance(alice, '1000.0000')
    render(headerFor(null))

    changeVisibility('visible')
    fireEvent.focus(window)

    await new Promise(resolve => setTimeout(resolve, 20))
    expect(ledger.requestCount()).toBe(0)
  })

  it('stops re-reading once the user signs out', async () => {
    mockBalance(alice, '1000.0000')
    const { rerender } = render(headerFor(alice))
    expect(await screen.findByLabelText('available balance')).toHaveTextContent('1,000 credits')

    rerender(headerFor(null))
    const ledger = mockBalance(alice, '1250.0000')
    changeVisibility('visible')
    fireEvent.focus(window)

    await new Promise(resolve => setTimeout(resolve, 20))
    expect(ledger.requestCount()).toBe(0)
  })
})
