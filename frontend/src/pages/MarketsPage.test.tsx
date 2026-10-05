import { StrictMode } from 'react'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it } from 'vitest'
import { AuthContext } from '../context/AuthContext'
import type { User } from '../context/AuthContext'
import { server } from '../test/server'
import MarketsPage from './MarketsPage'

const MARKET_BASE = 'http://localhost:8001'
const SNAPSHOT = 'http://localhost:8003/ledger/markets/:id/snapshot'

const trader: User = { id: '1', username: 'alice', email: 'alice@smu.edu.sg', role: 'trader', created_at: '2026-01-01' }

// A market's outcomes as GET /public/markets lists them, with ids
// `<market>-<position>` so a snapshot can name them.
function outcomesFor(marketId: string, labels: string[]) {
  return labels.map((label, position) => ({ id: `${marketId}-${position}`, position, label }))
}

const mockMarkets = [
  { id: 'a1', status: 'open',   question: 'Will SMU win SUNIG?',          close_time: '2099-01-05T12:00:00Z', outcomes: outcomesFor('a1', ['Yes', 'No']) },
  { id: 'b2', status: 'closed', question: 'Will inflation fall below 2%?', close_time: '2025-01-01T00:00:00Z', outcomes: outcomesFor('b2', ['Yes', 'No']) },
]

// The ledger's snapshot for a market, pricing its outcomes in position order.
function snapshotFor(marketId: string, prices: string[]) {
  return {
    market_id: marketId,
    state_version: 3,
    prices: prices.map((price, position) => ({ outcome_id: `${marketId}-${position}`, position, price })),
    occurred_at: '2026-09-15T09:12:44.318000+00:00',
  }
}

// The link that wraps the card holding this question.
function cardFor(question: string): HTMLElement {
  const card = screen.getByText(question).closest('a')
  if (!card) throw new Error(`no card for ${question}`)
  return card
}

// Under StrictMode, as main.tsx renders the app: its double mount is what a
// price hook that marks a market fetched too early gets wrong.
function renderPage() {
  return render(
    <StrictMode>
      <AuthContext.Provider value={{ user: trader, login: () => {}, logout: async () => {} }}>
        <MemoryRouter initialEntries={['/markets']}>
          <Routes>
            <Route path="/markets" element={<MarketsPage />} />
            <Route path="/markets/:id" element={<p>market detail</p>} />
          </Routes>
        </MemoryRouter>
      </AuthContext.Provider>
    </StrictMode>,
  )
}

// #104: page one carries a cursor; the request quoting it gets page two.
function mockTwoPages(secondPage: () => Response | Promise<Response>) {
  server.use(
    http.get(`${MARKET_BASE}/public/markets`, ({ request }) =>
      new URL(request.url).searchParams.get('cursor') === 'page-2'
        ? secondPage()
        : HttpResponse.json({ markets: [mockMarkets[0]], next_cursor: 'page-2' }),
    ),
  )
}

describe('MarketsPage', () => {
  // Every card asks the ledger for its prices; tests about other things get a
  // plain answer rather than an unhandled request.
  beforeEach(() => {
    server.use(
      http.get(SNAPSHOT, ({ params }) => HttpResponse.json(snapshotFor(String(params.id), ['0.5000', '0.5000']))),
    )
  })

  it('shows a card for each market returned by the API', async () => {
    server.use(
      http.get(`${MARKET_BASE}/public/markets`, () =>
        HttpResponse.json({ markets: mockMarkets, next_cursor: null }),
      ),
    )
    renderPage()
    expect(await screen.findByText('Will SMU win SUNIG?')).toBeInTheDocument()
    expect(screen.getByText('Will inflation fall below 2%?')).toBeInTheDocument()
  })

  it('shows status badges for each market', async () => {
    server.use(
      http.get(`${MARKET_BASE}/public/markets`, () =>
        HttpResponse.json({ markets: mockMarkets, next_cursor: null }),
      ),
    )
    renderPage()
    await screen.findByText('Will SMU win SUNIG?')
    expect(screen.getByText('Open')).toBeInTheDocument()
    // A past close_time also renders "Closed" in the timestamp slot, so two elements match.
    expect(screen.getAllByText('Closed').length).toBeGreaterThanOrEqual(1)
  })

  it('does not show a future closing date on a market closed early', async () => {
    // An admin can close a market before its close_time, and close_time stays
    // in the future — so the card must go by the status, not only the clock.
    server.use(
      http.get(`${MARKET_BASE}/public/markets`, () =>
        HttpResponse.json({
          markets: [{
            id: 'c3', status: 'closed', question: 'Closed early by an admin?', close_time: '2099-01-05T12:00:00Z',
            outcomes: outcomesFor('c3', ['Yes', 'No']),
          }],
          next_cursor: null,
        }),
      ),
    )
    renderPage()
    await screen.findByText('Closed early by an admin?')
    expect(screen.queryByText(/closes/i)).not.toBeInTheDocument()
  })

  it('shows an empty state when no markets are returned', async () => {
    server.use(
      http.get(`${MARKET_BASE}/public/markets`, () =>
        HttpResponse.json({ markets: [], next_cursor: null }),
      ),
    )
    renderPage()
    expect(await screen.findByText(/no markets available/i)).toBeInTheDocument()
  })

  it('shows an error when the API fails', async () => {
    server.use(
      http.get(`${MARKET_BASE}/public/markets`, () => HttpResponse.error()),
    )
    renderPage()
    expect(await screen.findByRole('alert')).toHaveTextContent(/failed to load/i)
  })

  it('navigates to the market detail page when a card is clicked', async () => {
    server.use(
      http.get(`${MARKET_BASE}/public/markets`, () =>
        HttpResponse.json({ markets: [mockMarkets[0]], next_cursor: null }),
      ),
    )
    const actor = userEvent.setup()
    renderPage()
    await actor.click(await screen.findByText('Will SMU win SUNIG?'))
    expect(screen.getByText('market detail')).toBeInTheDocument()
  })

  // [X-1] #126 AC 1 and AC 3. 0.1235 is a price a float would show as 12.3%.
  it("shows each card's YES/NO prices from that market's snapshot", async () => {
    server.use(
      http.get(`${MARKET_BASE}/public/markets`, () => HttpResponse.json({ markets: mockMarkets, next_cursor: null })),
      http.get(SNAPSHOT, ({ params }) =>
        params.id === 'a1'
          ? HttpResponse.json(snapshotFor('a1', ['0.1235', '0.8765']))
          : HttpResponse.json(snapshotFor('b2', ['0.7000', '0.3000'])),
      ),
    )
    renderPage()
    await screen.findByText('Will SMU win SUNIG?')

    const open = cardFor('Will SMU win SUNIG?')
    expect(await within(open).findByLabelText('Yes price')).toHaveTextContent('12.4%')
    expect(within(open).getByLabelText('No price')).toHaveTextContent('87.7%')

    // A closed market still has a last price, and shows it.
    const closed = cardFor('Will inflation fall below 2%?')
    expect(await within(closed).findByLabelText('Yes price')).toHaveTextContent('70.0%')
    expect(within(closed).getByLabelText('No price')).toHaveTextContent('30.0%')
  })

  // [X-1] #126 AC 2: one market's price failing does not take its card, or
  // anyone else's price, with it.
  it('shows a dash for a price that cannot be loaded, and still shows the market', async () => {
    server.use(
      http.get(`${MARKET_BASE}/public/markets`, () => HttpResponse.json({ markets: mockMarkets, next_cursor: null })),
      http.get(SNAPSHOT, ({ params }) =>
        params.id === 'a1'
          ? HttpResponse.json(
              { error: { code: 'market_terms_unavailable', message: 'Try again.' } },
              { status: 503 },
            )
          : HttpResponse.json(snapshotFor('b2', ['0.7000', '0.3000'])),
      ),
    )
    renderPage()
    await screen.findByText('Will SMU win SUNIG?')

    const closed = cardFor('Will inflation fall below 2%?')
    expect(await within(closed).findByLabelText('Yes price')).toHaveTextContent('70.0%')

    const failed = cardFor('Will SMU win SUNIG?')
    expect(within(failed).getByText('Will SMU win SUNIG?')).toBeInTheDocument()
    expect(within(failed).getByLabelText('Yes price')).toHaveTextContent('—')
    expect(within(failed).getByLabelText('No price')).toHaveTextContent('—')
  })

  // #214: a market can have any labels and more than two outcomes, and the
  // card names each one from the list response, priced by its own id.
  it('shows every outcome of a market by its own label, with its own price', async () => {
    const final = {
      id: 'd4', status: 'open', question: 'Who wins the NBA Finals?', close_time: '2099-06-01T00:00:00Z',
      outcomes: outcomesFor('d4', ['Lakers', 'Celtics', 'Draw']),
    }
    server.use(
      http.get(`${MARKET_BASE}/public/markets`, () => HttpResponse.json({ markets: [final], next_cursor: null })),
      http.get(SNAPSHOT, () => HttpResponse.json(snapshotFor('d4', ['0.5000', '0.3000', '0.2000']))),
    )
    renderPage()
    await screen.findByText('Who wins the NBA Finals?')
    const card = cardFor('Who wins the NBA Finals?')

    expect(await within(card).findByLabelText('Lakers price')).toHaveTextContent('50.0%')
    expect(within(card).getByLabelText('Celtics price')).toHaveTextContent('30.0%')
    expect(within(card).getByLabelText('Draw price')).toHaveTextContent('20.0%')
    expect(within(card).queryByLabelText('Yes price')).not.toBeInTheDocument()
  })

  // A price goes beside the outcome its id names, not the one at the same
  // place in the array: listed in reverse, each still lands on its own label.
  // An outcome the snapshot does not name shows a dash, and only that one.
  it('matches each price to its outcome by id', async () => {
    const final = {
      id: 'e5', status: 'open', question: 'Lakers or Celtics?', close_time: '2099-06-01T00:00:00Z',
      outcomes: outcomesFor('e5', ['Lakers', 'Celtics', 'Draw']),
    }
    server.use(
      http.get(`${MARKET_BASE}/public/markets`, () => HttpResponse.json({ markets: [final], next_cursor: null })),
      http.get(SNAPSHOT, () => HttpResponse.json({
        market_id: 'e5',
        state_version: 3,
        prices: [
          { outcome_id: 'e5-1', position: 1, price: '0.3000' },
          { outcome_id: 'e5-0', position: 0, price: '0.7000' },
        ],
        occurred_at: '2026-09-15T09:12:44.318000+00:00',
      })),
    )
    renderPage()
    await screen.findByText('Lakers or Celtics?')
    const card = cardFor('Lakers or Celtics?')

    expect(await within(card).findByLabelText('Lakers price')).toHaveTextContent('70.0%')
    expect(within(card).getByLabelText('Celtics price')).toHaveTextContent('30.0%')
    expect(within(card).getByLabelText('Draw price')).toHaveTextContent('—')
  })

  // [X-1] #104: Load more appends the next page, keeps the first, and goes
  // away when the server says there is nothing after it.
  it('appends the next page when Load more is clicked', async () => {
    mockTwoPages(() => HttpResponse.json({ markets: [mockMarkets[1]], next_cursor: null }))
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will SMU win SUNIG?')
    expect(screen.queryByText('Will inflation fall below 2%?')).not.toBeInTheDocument()

    await actor.click(screen.getByRole('button', { name: /load more/i }))

    expect(await screen.findByText('Will inflation fall below 2%?')).toBeInTheDocument()
    expect(screen.getByText('Will SMU win SUNIG?')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /load more/i })).not.toBeInTheDocument()
  })

  // [X-1] #104: "Error on load-more keeps already-shown cards", and the
  // trader can try again.
  it('keeps the cards already shown when loading more fails, and loads them on a retry', async () => {
    let secondPageRequests = 0
    mockTwoPages(() => {
      secondPageRequests += 1
      return secondPageRequests === 1
        ? HttpResponse.error()
        : HttpResponse.json({ markets: [mockMarkets[1]], next_cursor: null })
    })
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will SMU win SUNIG?')

    await actor.click(screen.getByRole('button', { name: /load more/i }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/couldn't load more markets/i)
    expect(screen.getByText('Will SMU win SUNIG?')).toBeInTheDocument()

    await actor.click(screen.getByRole('button', { name: /load more/i }))

    expect(await screen.findByText('Will inflation fall below 2%?')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  // #104 Review Focus 2: a double click must not fetch, or append, a page twice.
  it('asks for the next page once however fast Load more is clicked', async () => {
    let release!: () => void
    const held = new Promise<void>(resolve => { release = resolve })
    let secondPageRequests = 0
    mockTwoPages(async () => {
      secondPageRequests += 1
      await held
      return HttpResponse.json({ markets: [mockMarkets[1]], next_cursor: null })
    })
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will SMU win SUNIG?')

    await actor.dblClick(screen.getByRole('button', { name: /load more/i }))
    release()

    expect(await screen.findByText('Will inflation fall below 2%?')).toBeInTheDocument()
    expect(screen.getAllByText('Will inflation fall below 2%?')).toHaveLength(1)
    expect(secondPageRequests).toBe(1)
  })

  // StrictMode (on in main.tsx) asks for page one twice. Its first answer
  // arriving late must not replace the list after Load more has added to it.
  it('keeps the appended page when the first mount\'s answer arrives late', async () => {
    let release!: () => void
    const held = new Promise<void>(resolve => { release = resolve })
    let firstPageRequests = 0
    server.use(
      http.get(`${MARKET_BASE}/public/markets`, async ({ request }) => {
        if (new URL(request.url).searchParams.get('cursor') === 'page-2') {
          return HttpResponse.json({ markets: [mockMarkets[1]], next_cursor: null })
        }
        firstPageRequests += 1
        if (firstPageRequests === 1) await held
        return HttpResponse.json({ markets: [mockMarkets[0]], next_cursor: 'page-2' })
      }),
    )
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will SMU win SUNIG?')
    await actor.click(screen.getByRole('button', { name: /load more/i }))
    await screen.findByText('Will inflation fall below 2%?')

    release()

    // Long enough for the held answer to land and render, if it is going to.
    await new Promise(resolve => setTimeout(resolve, 50))
    expect(screen.getByText('Will inflation fall below 2%?')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /load more/i })).not.toBeInTheDocument()
  })

  // #104 amendment D: the cards already on screen have their prices; Load
  // more asks the ledger only about the markets it added.
  it('fetches prices only for the markets Load more adds', async () => {
    const snapshotRequests: string[] = []
    mockTwoPages(() => HttpResponse.json({ markets: [mockMarkets[1]], next_cursor: null }))
    server.use(
      http.get(SNAPSHOT, ({ params }) => {
        snapshotRequests.push(String(params.id))
        return params.id === 'a1'
          ? HttpResponse.json(snapshotFor('a1', ['0.1235', '0.8765']))
          : HttpResponse.json(snapshotFor('b2', ['0.7000', '0.3000']))
      }),
    )
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will SMU win SUNIG?')
    expect(await within(cardFor('Will SMU win SUNIG?')).findByLabelText('Yes price')).toHaveTextContent('12.4%')
    snapshotRequests.length = 0

    await actor.click(screen.getByRole('button', { name: /load more/i }))

    await screen.findByText('Will inflation fall below 2%?')
    expect(await within(cardFor('Will inflation fall below 2%?')).findByLabelText('Yes price')).toHaveTextContent('70.0%')
    expect(snapshotRequests).toEqual(['b2'])
    // The first card keeps the price it already had.
    expect(within(cardFor('Will SMU win SUNIG?')).getByLabelText('Yes price')).toHaveTextContent('12.4%')
  })
})
