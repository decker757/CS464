import { StrictMode } from 'react'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import type { MarketOverviewRow } from '../../api/marketApi'
import type { User } from '../../context/AuthContext'
import { holdUntilReleased } from '../../test/holdUntilReleased'
import { WithProviders } from '../../test/renderWithProviders'
import { server } from '../../test/server'
import AdminMarketsPage from './AdminMarketsPage'

const OVERVIEW = 'http://localhost:8001/markets/overview'

const admin: User = { id: 'u1', username: 'admin1', email: 'admin@smu.edu.sg', role: 'admin', created_at: '2026-01-01' }

const OTHER_ADMIN_ID = 'u2'

// The admin's own markets, one in each status, in the order the server sends
// them: soonest close first, no close time last.
const mockMarkets: MarketOverviewRow[] = [
  { id: 'd4', creator_id: admin.id, status: 'closed', question: 'Will inflation fall below 2%?', close_time: '2025-01-01T00:00:00Z' },
  { id: 'f6', creator_id: admin.id, status: 'approved', question: 'Did it rain yesterday?', close_time: '2025-05-01T00:00:00Z' },
  { id: 'e5', creator_id: admin.id, status: 'pending_resolution', question: 'Will it rain?', close_time: '2025-06-01T00:00:00Z' },
  { id: 'c3', creator_id: admin.id, status: 'open', question: 'Will SMU win SUNIG?', close_time: '2099-01-05T12:00:00Z' },
  { id: 'b2', creator_id: admin.id, status: 'submitted', question: 'Will X happen?', close_time: '2099-03-01T00:00:00Z' },
  { id: 'a1', creator_id: admin.id, status: 'draft', question: 'Untitled draft', close_time: null },
]

type Counts = Record<MarketOverviewRow['status'], number>

const ONE_OF_EACH: Counts = { draft: 1, submitted: 1, open: 1, closed: 1, pending_resolution: 1, approved: 1 }

// Under StrictMode, as main.tsx renders the app: its double mount asks for page
// one twice, which is what a page that keeps a late answer gets wrong.
function renderPage() {
  return render(
    <StrictMode>
      <WithProviders user={admin}>
        <MemoryRouter initialEntries={['/admin/markets']}>
          <Routes>
            <Route path="/admin/markets" element={<AdminMarketsPage />} />
            <Route path="/admin/markets/:id/propose-outcome" element={<p>propose outcome</p>} />
          </Routes>
        </MemoryRouter>
      </WithProviders>
    </StrictMode>,
  )
}

// The counts the server would send for these rows: every status, zero when none.
function countsOf(markets: MarketOverviewRow[]): Counts {
  const counts: Counts = { draft: 0, submitted: 0, open: 0, closed: 0, pending_resolution: 0, approved: 0 }
  for (const market of markets) counts[market.status] += 1
  return counts
}

// GET /markets/overview as the server answers it: the rows under `?status=`
// (every row without one), on one page, with counts over every row whatever
// the filter.
function mockList(markets: MarketOverviewRow[]) {
  const counts = countsOf(markets)
  server.use(
    http.get(OVERVIEW, ({ request }) => {
      const status = new URL(request.url).searchParams.get('status')
      const rows = status === null ? markets : markets.filter(market => market.status === status)
      return HttpResponse.json({ markets: rows, counts, next_cursor: null })
    }),
  )
}

// Page one is the first two of mockMarkets with a cursor; the request quoting
// `page-2` gets whatever `pageTwo` answers.
const PAGE_ONE = { markets: mockMarkets.slice(0, 2), counts: ONE_OF_EACH, next_cursor: 'page-2' }
const PAGE_TWO = { markets: [mockMarkets[2]], counts: ONE_OF_EACH, next_cursor: null }

function mockTwoPages(pageTwo: () => Response | Promise<Response>) {
  server.use(
    http.get(OVERVIEW, ({ request }) =>
      new URL(request.url).searchParams.get('cursor') === 'page-2' ? pageTwo() : HttpResponse.json(PAGE_ONE),
    ),
  )
}

function tab(name: RegExp): HTMLElement {
  return screen.getByRole('tab', { name })
}

describe('AdminMarketsPage', () => {
  it('shows a row for each of the caller\'s markets', async () => {
    mockList(mockMarkets)
    renderPage()
    expect(await screen.findByText('Will SMU win SUNIG?')).toBeInTheDocument()
    expect(screen.getByText('Will inflation fall below 2%?')).toBeInTheDocument()
    expect(screen.getByText('Untitled draft')).toBeInTheDocument()
  })

  it('marks only the markets the signed-in admin created as theirs', async () => {
    mockList([
      ...mockMarkets,
      { id: 'x9', creator_id: OTHER_ADMIN_ID, status: 'pending_resolution', question: 'Another admin\'s market?', close_time: '2025-07-01T00:00:00Z' },
    ])
    renderPage()
    const theirs = await screen.findByText('Another admin\'s market?')
    expect(theirs.closest('div')).not.toHaveTextContent(/created by you/i)
    expect(screen.getByRole('tab', { name: /all/i })).toHaveTextContent('7')
    expect(screen.getAllByText(/created by you/i)).toHaveLength(6)
  })

  it('shows a count per status on the filter tabs', async () => {
    mockList(mockMarkets)
    renderPage()
    await screen.findByText('Will SMU win SUNIG?')
    const tabs = screen.getAllByRole('tab')
    const byName = (name: string) => tabs.find(t => within(t).queryByText(name))
    expect(byName('All')).toHaveTextContent('6')
    expect(byName('Draft')).toHaveTextContent('1')
    expect(byName('Submitted')).toHaveTextContent('1')
    expect(byName('Open')).toHaveTextContent('1')
    expect(byName('Closed')).toHaveTextContent('1')
    expect(byName('Pending')).toHaveTextContent('1')
    expect(byName('Settled')).toHaveTextContent('1')
  })

  it('keeps the server\'s order: soonest close time first, no close time last', async () => {
    mockList(mockMarkets)
    renderPage()
    await screen.findByText('Will SMU win SUNIG?')
    const questions = screen.getAllByText(/^(Untitled draft|Will |Did )/).map(el => el.textContent)
    expect(questions).toEqual([
      'Will inflation fall below 2%?',
      'Did it rain yesterday?',
      'Will it rain?',
      'Will SMU win SUNIG?',
      'Will X happen?',
      'Untitled draft',
    ])
  })

  // #235: the filter is a request parameter; the page shows what the server returns for it.
  it('asks the server for the chosen status, and shows the rows it returns', async () => {
    const statuses: (string | null)[] = []
    server.use(
      http.get(OVERVIEW, ({ request }) => {
        const status = new URL(request.url).searchParams.get('status')
        statuses.push(status)
        const rows = status === 'open' ? [mockMarkets[3]] : mockMarkets
        return HttpResponse.json({ markets: rows, counts: ONE_OF_EACH, next_cursor: null })
      }),
    )
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will inflation fall below 2%?')

    await actor.click(tab(/^open/i))

    expect(await screen.findByText('Will SMU win SUNIG?')).toBeInTheDocument()
    expect(screen.queryByText('Will inflation fall below 2%?')).not.toBeInTheDocument()
    expect(statuses.at(-1)).toBe('open')
  })

  it('shows an empty state when a filter matches no markets', async () => {
    mockList(mockMarkets.filter(m => m.status !== 'draft'))
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will SMU win SUNIG?')

    expect(screen.getByRole('tab', { name: /draft/i })).toHaveTextContent('0')
    await actor.click(screen.getByRole('tab', { name: /draft/i }))

    expect(await screen.findByText(/no markets in this status/i)).toBeInTheDocument()
  })

  it('shows an empty state when no markets are returned', async () => {
    mockList([])
    renderPage()
    expect(await screen.findByText(/no markets in this status/i)).toBeInTheDocument()
  })

  it('shows an error when the API fails', async () => {
    server.use(http.get(OVERVIEW, () => HttpResponse.error()))
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent(/failed to load/i)
  })

  // #235: a new filter starts a new list, so the old list's failure does not
  // hide the new one's rows.
  it('shows the new filter\'s rows after the first page failed under the old one', async () => {
    server.use(
      http.get(OVERVIEW, ({ request }) =>
        new URL(request.url).searchParams.get('status') === 'open'
          ? HttpResponse.json({ markets: [mockMarkets[3]], counts: ONE_OF_EACH, next_cursor: null })
          : HttpResponse.error(),
      ),
    )
    const actor = userEvent.setup()
    renderPage()
    await screen.findByRole('alert')

    await actor.click(tab(/^open/i))

    expect(await screen.findByText('Will SMU win SUNIG?')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('does not show a future closing date on a market closed early', async () => {
    // An admin can close a market before its close_time, and close_time stays
    // in the future — so the row must go by the status, not only the clock.
    mockList([
      { id: 'g7', creator_id: admin.id, status: 'closed', question: 'Closed early by an admin?', close_time: '2099-01-05T12:00:00Z' },
    ])
    renderPage()
    await screen.findByText('Closed early by an admin?')
    expect(screen.queryByText(/closes/i)).not.toBeInTheDocument()
    // A past close_time also renders "Closed" in the status badge, so two elements match.
    expect(screen.getAllByText('Closed').length).toBeGreaterThanOrEqual(1)
  })

  it('offers Propose Outcome only for closed markets the admin created', async () => {
    // Only the creator may propose; for anyone else the request is a 404.
    mockList([
      ...mockMarkets,
      { id: 'y8', creator_id: OTHER_ADMIN_ID, status: 'closed', question: 'Another admin\'s closed market?', close_time: '2025-02-01T00:00:00Z' },
    ])
    renderPage()
    await screen.findByText('Another admin\'s closed market?')

    const links = screen.getAllByRole('link', { name: /propose outcome/i })
    expect(links).toHaveLength(1)
    expect(links[0]).toHaveAttribute('href', '/admin/markets/d4/propose-outcome')
  })

  it('navigates to the propose-outcome page for a closed market', async () => {
    mockList(mockMarkets)
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will inflation fall below 2%?')

    await actor.click(screen.getByRole('link', { name: /propose outcome/i }))

    expect(await screen.findByText('propose outcome')).toBeInTheDocument()
  })

  // #235: "The count cards show the latest response's counts (they cover every
  // market, not just the page)". All is their sum, not the rows on screen.
  it('counts All as every market the counts cover, not only the rows on the page', async () => {
    server.use(
      http.get(OVERVIEW, () => HttpResponse.json({ markets: [mockMarkets[0]], counts: ONE_OF_EACH, next_cursor: 'page-2' })),
    )
    renderPage()
    await screen.findByText('Will inflation fall below 2%?')

    expect(tab(/^all/i)).toHaveTextContent(/^All 6$/)
  })

  // #235: "A Load more button under the list fetches the next page by passing
  // cursor, and adds the new rows below", and "hidden when next_cursor is null".
  it('appends the next page when Load more is clicked, and hides the button on the last page', async () => {
    mockTwoPages(() => HttpResponse.json(PAGE_TWO))
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will inflation fall below 2%?')
    expect(screen.queryByText('Will it rain?')).not.toBeInTheDocument()

    await actor.click(screen.getByRole('button', { name: /load more/i }))

    expect(await screen.findByText('Will it rain?')).toBeInTheDocument()
    const questions = screen.getAllByText(/^(Will |Did )/).map(element => element.textContent)
    expect(questions).toEqual(['Will inflation fall below 2%?', 'Did it rain yesterday?', 'Will it rain?'])
    expect(screen.queryByRole('button', { name: /load more/i })).not.toBeInTheDocument()
  })

  // #235: the counts come from the latest response, Load more's included.
  it('shows the counts from the latest response', async () => {
    mockTwoPages(() => HttpResponse.json({ ...PAGE_TWO, counts: { ...ONE_OF_EACH, closed: 2 } }))
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will inflation fall below 2%?')
    expect(tab(/^closed/i)).toHaveTextContent(/^Closed 1$/)

    await actor.click(screen.getByRole('button', { name: /load more/i }))
    await screen.findByText('Will it rain?')

    expect(tab(/^closed/i)).toHaveTextContent(/^Closed 2$/)
    expect(tab(/^all/i)).toHaveTextContent(/^All 7$/)
  })

  // #235: "If loading more fails, the rows already shown stay and the admin can try again".
  it('keeps the rows already shown when loading more fails, and loads them on a retry', async () => {
    let pageTwoRequests = 0
    mockTwoPages(() => {
      pageTwoRequests += 1
      return pageTwoRequests === 1 ? HttpResponse.error() : HttpResponse.json(PAGE_TWO)
    })
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will inflation fall below 2%?')

    await actor.click(screen.getByRole('button', { name: /load more/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/couldn't load more markets/i)
    expect(screen.getByText('Will inflation fall below 2%?')).toBeInTheDocument()

    await actor.click(screen.getByRole('button', { name: /load more/i }))

    expect(await screen.findByText('Will it rain?')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  // #235: "a double click fetches once".
  it('asks for the next page once however fast Load more is clicked', async () => {
    const { held, release } = holdUntilReleased()
    let pageTwoRequests = 0
    mockTwoPages(async () => {
      pageTwoRequests += 1
      await held
      return HttpResponse.json(PAGE_TWO)
    })
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will inflation fall below 2%?')

    await actor.dblClick(screen.getByRole('button', { name: /load more/i }))
    release()

    expect(await screen.findByText('Will it rain?')).toBeInTheDocument()
    expect(screen.getAllByText('Will it rain?')).toHaveLength(1)
    expect(pageTwoRequests).toBe(1)
  })

  // StrictMode (on in main.tsx) asks for page one twice. Its first answer
  // arriving late must not replace the list after Load more has added to it.
  it('keeps the appended page when the first mount\'s answer arrives late', async () => {
    const { held, release } = holdUntilReleased()
    let pageOneRequests = 0
    server.use(
      http.get(OVERVIEW, async ({ request }) => {
        if (new URL(request.url).searchParams.get('cursor') === 'page-2') return HttpResponse.json(PAGE_TWO)
        pageOneRequests += 1
        if (pageOneRequests === 1) await held
        return HttpResponse.json(PAGE_ONE)
      }),
    )
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will inflation fall below 2%?')
    await actor.click(screen.getByRole('button', { name: /load more/i }))
    await screen.findByText('Will it rain?')

    release()

    // Long enough for the held answer to land and render, if it is going to.
    await new Promise(resolve => setTimeout(resolve, 50))
    expect(screen.getByText('Will it rain?')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /load more/i })).not.toBeInTheDocument()
  })

  // #235: "Changing the status filter starts again from page 1 (clear the list and the cursor)".
  it('starts again from page one when the status filter changes', async () => {
    const { held, release } = holdUntilReleased()
    const cursors: (string | null)[] = []
    server.use(
      http.get(OVERVIEW, async ({ request }) => {
        const params = new URL(request.url).searchParams
        cursors.push(params.get('cursor'))
        if (params.get('status') === 'open') {
          await held
          return HttpResponse.json({ markets: [mockMarkets[3]], counts: ONE_OF_EACH, next_cursor: null })
        }
        if (params.get('cursor') === 'page-2') return HttpResponse.json(PAGE_TWO)
        return HttpResponse.json(PAGE_ONE)
      }),
    )
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will inflation fall below 2%?')
    await actor.click(screen.getByRole('button', { name: /load more/i }))
    await screen.findByText('Will it rain?')

    await actor.click(tab(/^open/i))

    // While the new filter's first page is on its way, nothing of the old list shows.
    expect(screen.getByText('Loading markets…')).toBeInTheDocument()
    expect(screen.queryByText('Will inflation fall below 2%?')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /load more/i })).not.toBeInTheDocument()

    release()

    expect(await screen.findByText('Will SMU win SUNIG?')).toBeInTheDocument()
    expect(screen.queryByText('Will inflation fall below 2%?')).not.toBeInTheDocument()
    expect(screen.queryByText('Will it rain?')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /load more/i })).not.toBeInTheDocument()
    expect(cursors.at(-1)).toBeNull()
  })

  // A page asked for under the old filter must not land under the new one, and
  // the new filter's own Load more is not left waiting on it.
  it('drops a page that arrives after the status filter changed', async () => {
    const { held, release } = holdUntilReleased()
    server.use(
      http.get(OVERVIEW, async ({ request }) => {
        const params = new URL(request.url).searchParams
        if (params.get('status') === 'open') {
          return HttpResponse.json({ markets: [mockMarkets[3]], counts: ONE_OF_EACH, next_cursor: 'open-2' })
        }
        if (params.get('cursor') === 'page-2') {
          await held
          return HttpResponse.json(PAGE_TWO)
        }
        return HttpResponse.json(PAGE_ONE)
      }),
    )
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will inflation fall below 2%?')
    await actor.click(screen.getByRole('button', { name: /load more/i }))
    await actor.click(tab(/^open/i))
    await screen.findByText('Will SMU win SUNIG?')

    expect(screen.getByRole('button', { name: /load more/i })).toBeEnabled()

    release()

    // Long enough for the held page to land and render, if it is going to.
    await new Promise(resolve => setTimeout(resolve, 50))
    expect(screen.queryByText('Will it rain?')).not.toBeInTheDocument()
  })

  // #235: a failed Load more belongs to the list it was for; a new filter's
  // list starts without its alert.
  it('clears a failed Load more\'s alert when the status filter changes', async () => {
    server.use(
      http.get(OVERVIEW, ({ request }) => {
        const params = new URL(request.url).searchParams
        if (params.get('status') === 'open') {
          return HttpResponse.json({ markets: [mockMarkets[3]], counts: ONE_OF_EACH, next_cursor: 'open-2' })
        }
        if (params.get('cursor') === 'page-2') return HttpResponse.error()
        return HttpResponse.json(PAGE_ONE)
      }),
    )
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will inflation fall below 2%?')
    await actor.click(screen.getByRole('button', { name: /load more/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/couldn't load more markets/i)

    await actor.click(tab(/^open/i))

    expect(await screen.findByText('Will SMU win SUNIG?')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /load more/i })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  // DECISIONS.md, "The admin overview pages by keyset, settled markets last, …":
  // a draft edited, or a market settled, mid-scroll can come back on a later page.
  it('shows a market once when a later page repeats it', async () => {
    mockTwoPages(() => HttpResponse.json({ ...PAGE_TWO, markets: [mockMarkets[1], mockMarkets[2]] }))
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Did it rain yesterday?')

    await actor.click(screen.getByRole('button', { name: /load more/i }))

    expect(await screen.findByText('Will it rain?')).toBeInTheDocument()
    expect(screen.getAllByText('Did it rain yesterday?')).toHaveLength(1)
  })
})
