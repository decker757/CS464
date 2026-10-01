import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'
import { server } from '../test/server'
import { AuthContext, type User } from './AuthContext'
import { BalanceProvider, useBalance } from './BalanceContext'

const LEDGER_BASE = 'http://localhost:8003'

const alice: User = { id: '1', username: 'alice', email: 'alice@smu.edu.sg', role: 'trader', created_at: '2026-01-01' }
const bob: User = { id: '2', username: 'bob', email: 'bob@smu.edu.sg', role: 'trader', created_at: '2026-01-01' }

function DisplayBalance() {
  const { balance, refetch } = useBalance()
  return (
    <>
      <p>{balance === undefined ? 'no balance' : balance === null ? 'failed' : balance}</p>
      <button onClick={() => refetch()}>Refetch</button>
    </>
  )
}

function mockBalance(user: User, balance: string) {
  server.use(
    http.get(`${LEDGER_BASE}/ledger/balances/me`, () =>
      HttpResponse.json({ user_id: user.id, account_id: 'acc-1', balance }),
    ),
  )
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

describe('BalanceContext', () => {
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
})
