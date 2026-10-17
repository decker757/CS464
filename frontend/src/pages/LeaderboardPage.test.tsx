import { StrictMode } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { User } from '../context/AuthContext'
import { WithProviders } from '../test/renderWithProviders'
import { server } from '../test/server'
import LeaderboardPage from './LeaderboardPage'

const LEADERBOARD = 'http://localhost:8003/ledger/leaderboard'

const trader: User = { id: '1', username: 'alice', email: 'alice@smu.edu.sg', role: 'trader', created_at: '2026-01-01' }

function page(overrides: Record<string, unknown> = {}) {
  return {
    computed_at: '2026-10-11T09:14:00.123456Z',
    stale: false,
    ranked_users: 3,
    me: { rank: 3, username: 'alice', net_worth: '1003.8689' },
    rows: [
      { rank: 1, username: 'michelle_l', net_worth: '1431.3568', is_me: false },
      { rank: 2, username: 'ernest_t', net_worth: '1003.8689', is_me: false },
      { rank: 3, username: 'alice', net_worth: '1003.8689', is_me: true },
    ],
    next_cursor: null,
    has_more: false,
    ...overrides,
  }
}

function mockLeaderboard(body: Record<string, unknown>) {
  server.use(http.get(LEADERBOARD, () => HttpResponse.json(body)))
}

function renderPage() {
  return render(
    <StrictMode>
      <WithProviders user={trader}>
        <MemoryRouter initialEntries={['/leaderboard']}>
          <Routes>
            <Route path="/leaderboard" element={<LeaderboardPage />} />
          </Routes>
        </MemoryRouter>
      </WithProviders>
    </StrictMode>,
  )
}

describe('LeaderboardPage', () => {
  it('shows each row with its rank, username and net worth', async () => {
    mockLeaderboard(page())
    renderPage()

    expect(await screen.findByText('michelle_l')).toBeInTheDocument()
    expect(screen.getByText('ernest_t')).toBeInTheDocument()
    expect(screen.getByText('1,431.3568')).toBeInTheDocument()
    expect(screen.getAllByText('1,003.8689')).toHaveLength(2)
  })

  it('ranks from highest to lowest net worth, in the server\'s own order', async () => {
    mockLeaderboard(page())
    renderPage()

    await screen.findByText('michelle_l')
    const rows = screen.getAllByRole('row').slice(1)
    expect(rows[0]).toHaveTextContent('michelle_l')
    expect(rows[1]).toHaveTextContent('ernest_t')
    expect(rows[2]).toHaveTextContent('alice')
  })

  it('marks only the row whose is_me is true, never by comparing usernames', async () => {
    // ledger-service.md is explicit: match on is_me, not the username — a
    // row named "alice" with is_me false must not light up just because it
    // happens to equal the caller's own username elsewhere in the fixture.
    mockLeaderboard(page({
      rows: [
        { rank: 1, username: 'alice', net_worth: '1431.3568', is_me: false },
        { rank: 2, username: 'alice2', net_worth: '1003.8689', is_me: true },
      ],
    }))
    renderPage()

    await screen.findByText('alice2')
    const [impostorRow, meRow] = screen.getAllByRole('row').slice(1)
    expect(impostorRow).not.toHaveAttribute('data-is-me')
    expect(meRow).toHaveAttribute('data-is-me', 'true')
    // The highlight is colour, which a screen reader does not read — the
    // marker a non-visual user gets is this text, on the right row only.
    expect(impostorRow).not.toHaveTextContent('(you)')
    expect(meRow).toHaveTextContent('(you)')
  })

  it('shows only the username, never an email or an id the server sends alongside it', async () => {
    // #38's AC: emails and other private account information are never
    // exposed. The server never actually sends these on this route, but the
    // test proves the page renders only the fields it names, not whatever
    // a future "helpful" extra field on the row happens to carry.
    mockLeaderboard(page({
      rows: [
        { rank: 1, username: 'michelle_l', net_worth: '1431.3568', is_me: false, email: 'michelle_l@smu.edu.sg', user_id: 'u-42' },
      ],
    }))
    renderPage()

    await screen.findByText('michelle_l')
    expect(screen.queryByText('michelle_l@smu.edu.sg')).not.toBeInTheDocument()
    expect(screen.queryByText('u-42')).not.toBeInTheDocument()
  })

  it('shows when the board was last updated', async () => {
    mockLeaderboard(page())
    renderPage()

    expect(await screen.findByText(/updated/i)).toBeInTheDocument()
    expect(screen.getByText(/3 ranked/i)).toBeInTheDocument()
  })

  it('shows a stale note when the board has not refreshed recently, without treating it as an error', async () => {
    mockLeaderboard(page({ stale: true }))
    renderPage()

    expect(await screen.findByText(/may be out of date/i)).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('shows a not-enough-data state when no ranking has ever been computed', async () => {
    // ledger-service.md: computed_at null and me null together mean "no
    // ranking yet", distinct from "not ranked yet" (a ranking exists, the
    // caller just is not on it, which still renders the table, below).
    mockLeaderboard(page({ computed_at: null, stale: true, ranked_users: 0, me: null, rows: [] }))
    renderPage()

    expect(await screen.findByText(/not enough activity/i)).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('shows the board with no highlighted row when the caller is not ranked yet', async () => {
    mockLeaderboard(page({ me: null, rows: page().rows.map(r => ({ ...r, is_me: false })) }))
    renderPage()

    expect(await screen.findByText('michelle_l')).toBeInTheDocument()
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.queryByText(/your rank/i)).not.toBeInTheDocument()
  })

  describe('the caller\'s own rank', () => {
    it('shows rank and net worth from me, matching the highlighted row, when it is on the loaded page', async () => {
      mockLeaderboard(page())
      renderPage()

      expect(await screen.findByText('Your rank: #3')).toBeInTheDocument()
      expect(screen.getAllByText('1,003.8689')).toHaveLength(2) // the summary and alice's row
    })

    it('shows rank and net worth from me even when the caller\'s row is not on the loaded page', async () => {
      // ledger-service.md: me comes from the ranking a page was served from,
      // whether or not that row is on this particular page — this is the
      // reason me exists separately from is_me at all.
      mockLeaderboard(page({
        me: { rank: 50, username: 'alice', net_worth: '12.5000' },
        rows: page().rows.map(r => ({ ...r, is_me: false })),
      }))
      renderPage()

      expect(await screen.findByText('Your rank: #50')).toBeInTheDocument()
      expect(screen.getByText('12.5000 credits')).toBeInTheDocument()
      expect(screen.queryByText('(you)')).not.toBeInTheDocument()
    })

    it('says nothing about a rank when the caller is not ranked yet', async () => {
      mockLeaderboard(page({ me: null, rows: page().rows.map(r => ({ ...r, is_me: false })) }))
      renderPage()

      await screen.findByText('michelle_l')
      expect(screen.queryByText(/your rank/i)).not.toBeInTheDocument()
    })

    it('keeps showing the caller\'s rank from page one after Load more adds a page that does not contain it', async () => {
      server.use(
        http.get(LEADERBOARD, ({ request }) =>
          new URL(request.url).searchParams.get('cursor') === 'page-2'
            ? HttpResponse.json(page({ rows: [{ rank: 4, username: 'bob', net_worth: '500.0000', is_me: false }], next_cursor: null, has_more: false }))
            : HttpResponse.json(page({ next_cursor: 'page-2', has_more: true })),
        ),
      )
      const actor = userEvent.setup()
      renderPage()
      await screen.findByText('Your rank: #3')

      await actor.click(screen.getByRole('button', { name: /load more/i }))

      await screen.findByText('bob')
      expect(screen.getByText('Your rank: #3')).toBeInTheDocument()
    })
  })

  it('shows an error when the API fails', async () => {
    server.use(http.get(LEADERBOARD, () => HttpResponse.error()))
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent(/failed to load/i)
  })

  it('appends the next page when Load more is clicked', async () => {
    server.use(
      http.get(LEADERBOARD, ({ request }) =>
        new URL(request.url).searchParams.get('cursor') === 'page-2'
          ? HttpResponse.json(page({ rows: [{ rank: 4, username: 'bob', net_worth: '500.0000', is_me: false }], next_cursor: null, has_more: false }))
          : HttpResponse.json(page({ next_cursor: 'page-2', has_more: true })),
      ),
    )
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('michelle_l')
    expect(screen.queryByText('bob')).not.toBeInTheDocument()

    await actor.click(screen.getByRole('button', { name: /load more/i }))

    expect(await screen.findByText('bob')).toBeInTheDocument()
    expect(screen.getByText('michelle_l')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /load more/i })).not.toBeInTheDocument()
  })

  it('keeps the rows already shown when loading more fails, and loads them on a retry', async () => {
    let calls = 0
    server.use(
      http.get(LEADERBOARD, ({ request }) => {
        if (new URL(request.url).searchParams.get('cursor') !== 'page-2') {
          return HttpResponse.json(page({ next_cursor: 'page-2', has_more: true }))
        }
        calls += 1
        return calls === 1
          ? HttpResponse.error()
          : HttpResponse.json(page({ rows: [{ rank: 4, username: 'bob', net_worth: '500.0000', is_me: false }], next_cursor: null, has_more: false }))
      }),
    )
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('michelle_l')

    await actor.click(screen.getByRole('button', { name: /load more/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/couldn't load more rankings/i)
    expect(screen.getByText('michelle_l')).toBeInTheDocument()

    await actor.click(screen.getByRole('button', { name: /load more/i }))

    expect(await screen.findByText('bob')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('silently restarts from page one when the ranking was refreshed mid-walk, rather than showing an error', async () => {
    // ledger-service.md: 409 leaderboard_refreshed means the cursor's ranking
    // has been replaced twice. The required recovery is to replace the whole
    // list with the new ranking's page one, not append to the old one or
    // tell the user Load more failed.
    server.use(
      http.get(LEADERBOARD, ({ request }) =>
        new URL(request.url).searchParams.get('cursor') === 'page-2'
          ? HttpResponse.json({ error: { code: 'leaderboard_refreshed', message: 'refreshed' } }, { status: 409 })
          : HttpResponse.json(page({
            rows: [{ rank: 1, username: 'newcomer', net_worth: '2000.0000', is_me: false }],
            next_cursor: 'page-2', has_more: true,
          })),
      ),
    )
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('newcomer')

    await actor.click(screen.getByRole('button', { name: /load more/i }))

    await screen.findByText('newcomer')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getAllByText('newcomer')).toHaveLength(1)
  })
})
