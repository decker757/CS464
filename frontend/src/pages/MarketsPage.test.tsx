import { StrictMode } from 'react'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import type { User } from '../context/AuthContext'
import { holdUntilReleased } from '../test/holdUntilReleased'
import { WithProviders } from '../test/renderWithProviders'
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

// The card holding this question: the whole panel, not the link inside it, so
// the Retry control that deliberately sits outside that link counts as part
// of the card (MarketCard, #431).
function cardFor(question: string): HTMLElement {
  const card = screen.getByText(question).closest('a')?.parentElement
  if (!card) throw new Error(`no card for ${question}`)
  return card
}

// Under StrictMode, as main.tsx renders the app: its double mount is what a
// price hook that marks a market fetched too early gets wrong.
function renderPage() {
  return render(
    <StrictMode>
      <WithProviders user={trader}>
        <MemoryRouter initialEntries={['/markets']}>
          <Routes>
            <Route path="/markets" element={<MarketsPage />} />
            <Route path="/markets/:id" element={<p>market detail</p>} />
          </Routes>
        </MemoryRouter>
      </WithProviders>
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
    // Scoped to the cards: the status filter buttons carry the same words.
    expect(within(cardFor('Will SMU win SUNIG?')).getByText('Open')).toBeInTheDocument()
    // A past close_time also renders "Closed" in the timestamp slot, so two elements match.
    expect(within(cardFor('Will inflation fall below 2%?')).getAllByText('Closed').length).toBeGreaterThanOrEqual(1)
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

  // [X-1] #364 AC 3: the failed list had no way out but a browser reload.
  it('loads the markets when Try again is clicked after a failure', async () => {
    let failing = true
    server.use(
      http.get(`${MARKET_BASE}/public/markets`, () =>
        failing ? HttpResponse.error() : HttpResponse.json({ markets: [mockMarkets[0]], next_cursor: null }),
      ),
    )
    const actor = userEvent.setup()
    renderPage()
    await screen.findByRole('alert')

    failing = false
    await actor.click(screen.getByRole('button', { name: /try again/i }))

    expect(await screen.findByText('Will SMU win SUNIG?')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  // [X-1] #364 AC 1: the prices refresh on their own, and the screen says when
  // the last one landed so a refresh that is failing reads as a stale time.
  it('says when the prices on screen were read', async () => {
    server.use(
      http.get(`${MARKET_BASE}/public/markets`, () => HttpResponse.json({ markets: [mockMarkets[0]], next_cursor: null })),
    )
    renderPage()
    await screen.findByText('Will SMU win SUNIG?')

    expect(await screen.findByText(/prices updated \d/i)).toBeInTheDocument()
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
    await waitFor(() => expect(within(open).getByLabelText('Yes price')).toHaveTextContent('12.4%'))
    expect(within(open).getByLabelText('No price')).toHaveTextContent('87.7%')

    // A closed market still has a last price, and shows it.
    const closed = cardFor('Will inflation fall below 2%?')
    await waitFor(() => expect(within(closed).getByLabelText('Yes price')).toHaveTextContent('70.0%'))
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
    await waitFor(() => expect(within(closed).getByLabelText('Yes price')).toHaveTextContent('70.0%'))

    const failed = cardFor('Will SMU win SUNIG?')
    expect(within(failed).getByText('Will SMU win SUNIG?')).toBeInTheDocument()
    expect(within(failed).getByLabelText('Yes price')).toHaveTextContent('—')
    expect(within(failed).getByLabelText('No price')).toHaveTextContent('—')
    // [X-1] #431 AC 1: the dash says a number is missing, and the card now
    // says which kind of missing it is. A card still waiting for its answer
    // says the other thing.
    expect(within(failed).getByRole('alert')).toHaveTextContent('Prices could not be loaded.')
    expect(within(failed).queryByText('Loading prices…')).not.toBeInTheDocument()
    // [X-1] #445 AC 3: a price nobody could read is not a market nobody has
    // traded, and must not borrow its wording.
    expect(within(failed).queryByText(/no trades yet/i)).not.toBeInTheDocument()
  })

  // [X-1] #431 AC 1: the failed card's own way back. Telling the two apart is
  // only worth doing because one of them can be retried, and a market that
  // has stopped trading is never read again by itself.
  it("reads a card's prices again when its Retry is clicked", async () => {
    let failing = true
    server.use(
      http.get(`${MARKET_BASE}/public/markets`, () => HttpResponse.json({ markets: [mockMarkets[1]], next_cursor: null })),
      http.get(SNAPSHOT, () =>
        failing ? HttpResponse.error() : HttpResponse.json(snapshotFor('b2', ['0.7000', '0.3000'])),
      ),
    )
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will inflation fall below 2%?')
    const card = within(cardFor('Will inflation fall below 2%?'))
    await waitFor(() => expect(card.getByRole('alert')).toBeInTheDocument())

    failing = false
    await actor.click(card.getByRole('button', { name: 'Retry' }))

    await waitFor(() => expect(card.getByLabelText('Yes price')).toHaveTextContent('70.0%'))
    expect(card.queryByRole('alert')).not.toBeInTheDocument()
    expect(card.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument()
  })

  // [X-1] #445 AC 1: state_version 0 is the book saying nobody has ever traded
  // it, and its prices are the untouched LMSR seed. Rendering them puts a
  // uniform 50.0% on the card, which reads as a crowd split down the middle —
  // the opposite statement.
  it('invites the first trade on a market nobody has traded, rather than showing its seed price', async () => {
    server.use(
      http.get(`${MARKET_BASE}/public/markets`, () => HttpResponse.json({ markets: [mockMarkets[0]], next_cursor: null })),
      http.get(SNAPSHOT, () => HttpResponse.json({ ...snapshotFor('a1', ['0.5000', '0.5000']), state_version: 0 })),
    )
    renderPage()
    await screen.findByText('Will SMU win SUNIG?')
    const card = cardFor('Will SMU win SUNIG?')

    expect(await within(card).findByText(/no trades yet/i)).toBeInTheDocument()
    // No seed price, and no dash either: there is no number to show at all.
    expect(within(card).queryByLabelText('Yes price')).not.toBeInTheDocument()
    expect(within(card).queryByText('50.0%')).not.toBeInTheDocument()
    // The outcomes are still named (#214); only their prices are withheld.
    expect(within(card).getByText('Yes')).toBeInTheDocument()
    expect(within(card).getByText('No')).toBeInTheDocument()
  })

  // The same market nobody traded, after it stopped trading: still not a
  // 50/50 opinion, and no longer a market anybody can be the first to price.
  // A card inviting a trade on a closed market would be its own lie.
  it('does not invite a trade on a market that closed untraded', async () => {
    server.use(
      http.get(`${MARKET_BASE}/public/markets`, () => HttpResponse.json({ markets: [mockMarkets[1]], next_cursor: null })),
      http.get(SNAPSHOT, () => HttpResponse.json({ ...snapshotFor('b2', ['0.5000', '0.5000']), state_version: 0 })),
    )
    renderPage()
    await screen.findByText('Will inflation fall below 2%?')
    const card = cardFor('Will inflation fall below 2%?')

    expect(await within(card).findByText(/closed without a single trade/i)).toBeInTheDocument()
    expect(within(card).queryByText(/be the first/i)).not.toBeInTheDocument()
    expect(within(card).queryByLabelText('Yes price')).not.toBeInTheDocument()
  })

  // [X-1] #445 AC 2 and AC 4: a book that has moved says when, off the
  // browser's own clock so it lands in the reader's time zone.
  it('says how long ago a traded market last moved', async () => {
    const threeHoursAgo = new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString()
    server.use(
      http.get(`${MARKET_BASE}/public/markets`, () => HttpResponse.json({ markets: [mockMarkets[0]], next_cursor: null })),
      http.get(SNAPSHOT, () => HttpResponse.json({ ...snapshotFor('a1', ['0.6200', '0.3800']), occurred_at: threeHoursAgo })),
    )
    renderPage()
    await screen.findByText('Will SMU win SUNIG?')
    const card = cardFor('Will SMU win SUNIG?')

    await waitFor(() => expect(within(card).getByLabelText('Yes price')).toHaveTextContent('62.0%'))
    expect(within(card).getByText('Last trade 3h ago')).toBeInTheDocument()
    expect(within(card).queryByText(/no trades yet/i)).not.toBeInTheDocument()
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

    await waitFor(() => expect(within(card).getByLabelText('Lakers price')).toHaveTextContent('50.0%'))
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

    await waitFor(() => expect(within(card).getByLabelText('Lakers price')).toHaveTextContent('70.0%'))
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
    const { held, release } = holdUntilReleased()
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
    const { held, release } = holdUntilReleased()
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
    await waitFor(() => expect(within(cardFor('Will SMU win SUNIG?')).getByLabelText('Yes price')).toHaveTextContent('12.4%'))
    snapshotRequests.length = 0

    await actor.click(screen.getByRole('button', { name: /load more/i }))

    await screen.findByText('Will inflation fall below 2%?')
    await waitFor(() => expect(within(cardFor('Will inflation fall below 2%?')).getByLabelText('Yes price')).toHaveTextContent('70.0%'))
    expect(snapshotRequests).toEqual(['b2'])
    // The first card keeps the price it already had.
    expect(within(cardFor('Will SMU win SUNIG?')).getByLabelText('Yes price')).toHaveTextContent('12.4%')
  })

  // [X-2] #35: the server does the searching and the filtering (market-service.md),
  // so this stand-in applies q and status the way it does and the test follows
  // what the trader sees, not just what was sent.
  describe('search and filters', () => {
    function mockSearchableMarkets() {
      server.use(
        http.get(`${MARKET_BASE}/public/markets`, ({ request }) => {
          const params = new URL(request.url).searchParams
          const q = params.get('q')?.toLowerCase()
          const status = params.get('status')
          const matching = mockMarkets.filter(market =>
            (!q || market.question.toLowerCase().includes(q)) && (!status || market.status === status),
          )
          return HttpResponse.json({ markets: matching, next_cursor: null })
        }),
      )
    }

    it('narrows the list to markets whose question matches the search', async () => {
      mockSearchableMarkets()
      const actor = userEvent.setup()
      renderPage()
      await screen.findByText('Will inflation fall below 2%?')

      await actor.type(screen.getByRole('searchbox', { name: /search markets/i }), 'inflation{Enter}')

      await waitFor(() => expect(screen.queryByText('Will SMU win SUNIG?')).not.toBeInTheDocument())
      expect(screen.getByText('Will inflation fall below 2%?')).toBeInTheDocument()
    })

    it('narrows the list to one status', async () => {
      mockSearchableMarkets()
      const actor = userEvent.setup()
      renderPage()
      await screen.findByText('Will inflation fall below 2%?')

      await actor.click(screen.getByRole('radio', { name: 'Open' }))

      await waitFor(() => expect(screen.queryByText('Will inflation fall below 2%?')).not.toBeInTheDocument())
      expect(screen.getByText('Will SMU win SUNIG?')).toBeInTheDocument()
    })

    // #258 added `settled`, and `approved` now means approved and waiting for
    // payout, so the last pill has to ask for settled markets and not approved
    // ones. It reads "Resolved" to a trader (#431); `settled` is still the
    // status it sends.
    it('asks the server for settled markets when Resolved is chosen', async () => {
      const statusesSent: (string | null)[] = []
      const settled = {
        id: 's7', status: 'settled', question: 'Did the referendum pass?', close_time: '2025-02-01T00:00:00Z',
        outcomes: outcomesFor('s7', ['Yes', 'No']),
      }
      server.use(
        http.get(`${MARKET_BASE}/public/markets`, ({ request }) => {
          const status = new URL(request.url).searchParams.get('status')
          statusesSent.push(status)
          return HttpResponse.json({ markets: status === 'settled' ? [settled] : mockMarkets, next_cursor: null })
        }),
      )
      const actor = userEvent.setup()
      renderPage()
      await screen.findByText('Will SMU win SUNIG?')

      await actor.click(screen.getByRole('radio', { name: 'Resolved' }))

      expect(await screen.findByText('Did the referendum pass?')).toBeInTheDocument()
      expect(statusesSent.at(-1)).toBe('settled')
      expect(within(cardFor('Did the referendum pass?')).getByText('Resolved')).toBeInTheDocument()
    })

    // [X-1] #431 AC 3: the pills read the admin's lifecycle words, and
    // "Pending" and "Approved" are steps of a state machine that exists for a
    // rule (ADR 0016) a trader knows nothing about. Each pill still asks for
    // one status, which is all GET /public/markets accepts.
    it("names the resolution statuses in the trader's words and asks for the right one", async () => {
      const statusesSent: (string | null)[] = []
      server.use(
        http.get(`${MARKET_BASE}/public/markets`, ({ request }) => {
          statusesSent.push(new URL(request.url).searchParams.get('status'))
          return HttpResponse.json({ markets: mockMarkets, next_cursor: null })
        }),
      )
      const actor = userEvent.setup()
      renderPage()
      await screen.findByText('Will SMU win SUNIG?')
      const filters = within(screen.getByRole('radiogroup', { name: /filter by status/i }))

      expect(filters.queryByRole('radio', { name: 'Pending' })).not.toBeInTheDocument()
      expect(filters.queryByRole('radio', { name: 'Approved' })).not.toBeInTheDocument()
      expect(filters.getByRole('radio', { name: 'Awaiting payout' })).toBeInTheDocument()

      await actor.click(filters.getByRole('radio', { name: 'Resolving' }))

      await waitFor(() => expect(statusesSent.at(-1)).toBe('pending_resolution'))
      expect(screen.getByText('Status: Resolving ×')).toBeInTheDocument()
    })

    it('applies a search and a status together', async () => {
      mockSearchableMarkets()
      const actor = userEvent.setup()
      renderPage()
      await screen.findByText('Will inflation fall below 2%?')

      await actor.click(screen.getByRole('radio', { name: 'Open' }))
      await actor.type(screen.getByRole('searchbox', { name: /search markets/i }), 'inflation{Enter}')

      // The one open market does not mention inflation, and the one that does is closed.
      expect(await screen.findByText(/no markets match these filters/i)).toBeInTheDocument()
    })

    it('shows the active filters and clears them one at a time or all at once', async () => {
      mockSearchableMarkets()
      const actor = userEvent.setup()
      renderPage()
      await screen.findByText('Will inflation fall below 2%?')
      const active = () => screen.queryByRole('group', { name: /active filters/i })
      expect(active()).not.toBeInTheDocument()

      await actor.click(screen.getByRole('radio', { name: 'Closed' }))
      await actor.type(screen.getByRole('searchbox', { name: /search markets/i }), 'inflation{Enter}')
      expect(within(active()!).getByText('Search: inflation ×')).toBeInTheDocument()
      expect(within(active()!).getByText('Status: Closed ×')).toBeInTheDocument()

      await actor.click(screen.getByRole('button', { name: 'Clear search' }))
      expect(within(active()!).queryByText('Search: inflation ×')).not.toBeInTheDocument()
      expect(screen.getByRole('searchbox', { name: /search markets/i })).toHaveValue('')

      await actor.type(screen.getByRole('searchbox', { name: /search markets/i }), 'smu{Enter}')
      await actor.click(screen.getByRole('button', { name: 'Clear all' }))

      expect(active()).not.toBeInTheDocument()
      expect(await screen.findByText('Will SMU win SUNIG?')).toBeInTheDocument()
      expect(screen.getByText('Will inflation fall below 2%?')).toBeInTheDocument()
    })

    // "Clear all" empties the box as well as the applied search, including
    // text typed over that search and never submitted.
    it('empties a typed but unsubmitted search on Clear all', async () => {
      mockSearchableMarkets()
      const actor = userEvent.setup()
      renderPage()
      await screen.findByText('Will inflation fall below 2%?')

      await actor.click(screen.getByRole('radio', { name: 'Closed' }))
      await actor.type(screen.getByRole('searchbox', { name: /search markets/i }), 'inflation{Enter}')
      await actor.type(screen.getByRole('searchbox', { name: /search markets/i }), ' rate')
      await actor.click(screen.getByRole('button', { name: 'Clear all' }))

      expect(screen.getByRole('searchbox', { name: /search markets/i })).toHaveValue('')
    })

    // #442 AC 1: with one filter applied, its own chip already clears it, so a
    // "Clear all" beside it would be a second control doing the same thing.
    it('offers Clear all only when there are two filters to clear', async () => {
      mockSearchableMarkets()
      const actor = userEvent.setup()
      renderPage()
      await screen.findByText('Will inflation fall below 2%?')
      const clearAll = () => screen.queryByRole('button', { name: 'Clear all' })

      await actor.click(screen.getByRole('radio', { name: 'Closed' }))
      expect(screen.getByRole('button', { name: 'Clear status filter' })).toBeInTheDocument()
      expect(clearAll()).not.toBeInTheDocument()

      await actor.type(screen.getByRole('searchbox', { name: /search markets/i }), 'inflation{Enter}')
      expect(clearAll()).toBeInTheDocument()

      await actor.click(screen.getByRole('button', { name: 'Clear status filter' }))
      expect(screen.getByRole('button', { name: 'Clear search' })).toBeInTheDocument()
      expect(clearAll()).not.toBeInTheDocument()
    })

    // #442 AC 2: choosing a status replaces the one before it, so the pills
    // are radios, which say "one of these", and not toggle buttons, which say
    // "any of these".
    it('marks the chosen status as the one checked radio', async () => {
      mockSearchableMarkets()
      const actor = userEvent.setup()
      renderPage()
      await screen.findByText('Will inflation fall below 2%?')
      const filters = within(screen.getByRole('radiogroup', { name: 'Filter by status' }))
      expect(filters.getAllByRole('radio', { checked: true })).toEqual([filters.getByRole('radio', { name: 'All' })])

      await actor.click(filters.getByRole('radio', { name: 'Closed' }))

      expect(filters.getAllByRole('radio', { checked: true })).toEqual([filters.getByRole('radio', { name: 'Closed' })])
    })

    // A radio group's keyboard contract (WAI-ARIA's radio group pattern): the
    // arrows move the choice, not just the focus, and wrap at either end.
    it('moves the status choice with the arrow keys and wraps at either end', async () => {
      const statusesSent: (string | null)[] = []
      server.use(
        http.get(`${MARKET_BASE}/public/markets`, ({ request }) => {
          statusesSent.push(new URL(request.url).searchParams.get('status'))
          return HttpResponse.json({ markets: mockMarkets, next_cursor: null })
        }),
      )
      const actor = userEvent.setup()
      renderPage()
      await screen.findByText('Will SMU win SUNIG?')
      const filters = within(screen.getByRole('radiogroup', { name: 'Filter by status' }))
      function expectChosen(name: string) {
        expect(filters.getByRole('radio', { name })).toHaveFocus()
        expect(filters.getByRole('radio', { name })).toBeChecked()
      }
      await actor.click(filters.getByRole('radio', { name: 'All' }))

      await actor.keyboard('{ArrowRight}')
      expectChosen('Open')
      await actor.keyboard('{ArrowDown}')
      expectChosen('Closed')
      await waitFor(() => expect(statusesSent.at(-1)).toBe('closed'))
      await actor.keyboard('{ArrowUp}')
      expectChosen('Open')
      await actor.keyboard('{ArrowLeft}')
      expectChosen('All')
      await actor.keyboard('{ArrowLeft}')
      expectChosen('Resolved')
      await waitFor(() => expect(statusesSent.at(-1)).toBe('settled'))
      await actor.keyboard('{ArrowRight}')
      expectChosen('All')
    })

    // An arrow with Alt, Ctrl or Cmd held belongs to the browser or the
    // screen reader, not to the group: Alt+Left is the browser's Back.
    it('leaves an arrow with Alt, Ctrl or Cmd held to the browser', async () => {
      mockSearchableMarkets()
      const actor = userEvent.setup()
      renderPage()
      await screen.findByText('Will inflation fall below 2%?')
      const open = screen.getByRole('radio', { name: 'Open' })
      await actor.click(open)

      await actor.keyboard('{Alt>}{ArrowLeft}{/Alt}')
      expect(open).toBeChecked()
      await actor.keyboard('{Control>}{ArrowLeft}{/Control}')
      expect(open).toBeChecked()
      await actor.keyboard('{Meta>}{ArrowLeft}{/Meta}')
      expect(open).toBeChecked()
    })

    // The same pattern's one tab stop: Tab reaches the group at the chosen
    // pill and leaves it in one more press, instead of stopping on all six.
    it('gives the status group one tab stop, on the chosen pill', async () => {
      mockSearchableMarkets()
      const actor = userEvent.setup()
      renderPage()
      await screen.findByText('Will inflation fall below 2%?')
      await actor.click(screen.getByRole('radio', { name: 'Closed' }))

      screen.getByRole('button', { name: 'Search' }).focus()
      await actor.tab()
      expect(screen.getByRole('radio', { name: 'Closed' })).toHaveFocus()
      // The closing-time group (#442) comes next, at its own checked pill.
      await actor.tab()
      expect(screen.getByRole('radio', { name: 'Any time' })).toHaveFocus()
    })

    // A different status is a different list (MarketList's own comment): the
    // `key` on it must change, or a stale card from the old filter would keep
    // its old price forever instead of refetching.
    it('refetches prices for a market still shown after the status filter changes', async () => {
      let snapshotRequests = 0
      server.use(
        http.get(`${MARKET_BASE}/public/markets`, () => HttpResponse.json({ markets: [mockMarkets[0]], next_cursor: null })),
        http.get(SNAPSHOT, ({ params }) => {
          snapshotRequests += 1
          return HttpResponse.json(snapshotFor(String(params.id), snapshotRequests === 1 ? ['0.1200', '0.8800'] : ['0.7000', '0.3000']))
        }),
      )
      renderPage()
      await screen.findByText('Will SMU win SUNIG?')
      await waitFor(() => expect(within(cardFor('Will SMU win SUNIG?')).getByLabelText('Yes price')).toHaveTextContent('12.0%'))

      const actor = userEvent.setup()
      await actor.click(screen.getByRole('radio', { name: 'Open' }))

      await screen.findByText('Will SMU win SUNIG?')
      await waitFor(() => expect(within(cardFor('Will SMU win SUNIG?')).getByLabelText('Yes price')).toHaveTextContent('70.0%'))
    })

    it('starts a new search from the first page, not from the cursor of the old list', async () => {
      const cursorsSeen: (string | null)[] = []
      server.use(
        http.get(`${MARKET_BASE}/public/markets`, ({ request }) => {
          const params = new URL(request.url).searchParams
          cursorsSeen.push(params.get('cursor'))
          return HttpResponse.json({ markets: [mockMarkets[0]], next_cursor: params.get('q') ? null : 'page-2' })
        }),
      )
      const actor = userEvent.setup()
      renderPage()
      await screen.findByRole('button', { name: /load more/i })

      await actor.type(screen.getByRole('searchbox', { name: /search markets/i }), 'smu{Enter}')

      await waitFor(() => expect(screen.queryByRole('button', { name: /load more/i })).not.toBeInTheDocument())
      expect(cursorsSeen.at(-1)).toBeNull()
    })
  })
})

// #442: "A trader can narrow the list by when markets close." The window is
// worked out in the trader's own timezone, so the suite pins one with a DST
// change in it, and pins the clock to the instant each case is about. Only
// `Date` is faked, so userEvent's and MSW's own timers still run.
describe('MarketsPage closing-time presets', () => {
  const windowsSent: { closesFrom: string | null; closesBefore: string | null }[] = []

  beforeAll(() => {
    vi.stubEnv('TZ', 'America/New_York')
    // Setting TZ at run time is relied on only under the forks pool
    // (vite.config.ts). If the zone did not take, every literal below is wrong
    // in a way that depends on the machine's own zone: fail here instead.
    expect(new Date(2026, 9, 21).toISOString()).toBe('2026-10-21T04:00:00.000Z')
  })

  afterAll(() => {
    vi.unstubAllEnvs()
  })

  beforeEach(() => {
    windowsSent.length = 0
    server.use(
      http.get(SNAPSHOT, ({ params }) => HttpResponse.json(snapshotFor(String(params.id), ['0.5000', '0.5000']))),
      http.get(`${MARKET_BASE}/public/markets`, ({ request }) => {
        const params = new URL(request.url).searchParams
        const closesFrom = params.get('closes_from')
        windowsSent.push({ closesFrom, closesBefore: params.get('closes_before') })
        return HttpResponse.json({ markets: closesFrom ? [] : mockMarkets, next_cursor: null })
      }),
    )
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  function pinClock(instant: string) {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date(instant))
  }

  const closesFilter = () => within(screen.getByRole('radiogroup', { name: /filter by closing time/i }))

  // 22:30 on Wednesday 21 October in New York is already the 22nd in UTC, so
  // a window built from UTC days asks for the wrong day.
  it('asks for the markets closing today, from local midnight to the next', async () => {
    pinClock('2026-10-22T02:30:00Z')
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will SMU win SUNIG?')

    await actor.click(closesFilter().getByRole('radio', { name: 'Today' }))

    expect(await screen.findByText(/no markets match these filters/i)).toBeInTheDocument()
    expect(windowsSent.at(-1)).toEqual({ closesFrom: '2026-10-21T04:00:00.000Z', closesBefore: '2026-10-22T04:00:00.000Z' })
    expect(closesFilter().getByRole('radio', { name: 'Today' })).toBeChecked()
    expect(within(screen.getByRole('group', { name: /active filters/i })).getByText('Closes: Today ×')).toBeInTheDocument()
  })

  // Sunday 1 November 2026 is the day New York leaves summer time: the week
  // began on Monday at UTC-4 and ends at the next Monday at UTC-5, so a week
  // is not seven times 24 hours, and a Sunday still belongs to the week
  // before it.
  it('asks for this week as Monday to Monday in local time, even on a Sunday across a clock change', async () => {
    pinClock('2026-11-01T17:00:00Z')
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will SMU win SUNIG?')

    await actor.click(closesFilter().getByRole('radio', { name: 'This week' }))

    await waitFor(() =>
      expect(windowsSent.at(-1)).toEqual({ closesFrom: '2026-10-26T04:00:00.000Z', closesBefore: '2026-11-02T05:00:00.000Z' }),
    )
  })

  it('asks for this month, from its first day to the first day of the next, across a new year', async () => {
    pinClock('2026-12-15T15:00:00Z')
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will SMU win SUNIG?')

    await actor.click(closesFilter().getByRole('radio', { name: 'This month' }))

    await waitFor(() =>
      expect(windowsSent.at(-1)).toEqual({ closesFrom: '2026-12-01T05:00:00.000Z', closesBefore: '2027-01-01T05:00:00.000Z' }),
    )
  })

  // #442 point 2 is about single-select pills marked up as multi-select; these
  // are single-select and say so, as radios.
  it('holds one preset at a time, and its chip clears it back to Any time', async () => {
    pinClock('2026-10-22T02:30:00Z')
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will SMU win SUNIG?')
    expect(closesFilter().getByRole('radio', { name: 'Any time' })).toBeChecked()

    await actor.click(closesFilter().getByRole('radio', { name: 'Today' }))
    await actor.click(closesFilter().getByRole('radio', { name: 'This week' }))

    expect(closesFilter().getByRole('radio', { name: 'This week' })).toBeChecked()
    expect(closesFilter().getByRole('radio', { name: 'Today' })).not.toBeChecked()

    await actor.click(screen.getByRole('button', { name: 'Clear closing filter' }))

    expect(await screen.findByText('Will SMU win SUNIG?')).toBeInTheDocument()
    expect(windowsSent.at(-1)).toEqual({ closesFrom: null, closesBefore: null })
    expect(closesFilter().getByRole('radio', { name: 'Any time' })).toBeChecked()
  })

  it.each([
    ['ArrowRight', 'ArrowLeft'],
    ['ArrowDown', 'ArrowUp'],
  ])('moves the choice forward with %s and back with %s, as a radio group does', async (forward, back) => {
    pinClock('2026-10-22T02:30:00Z')
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will SMU win SUNIG?')

    await actor.click(closesFilter().getByRole('radio', { name: 'Today' }))
    await actor.keyboard(`{${forward}}`)

    expect(closesFilter().getByRole('radio', { name: 'This week' })).toBeChecked()
    expect(closesFilter().getByRole('radio', { name: 'This week' })).toHaveFocus()
    await waitFor(() =>
      expect(windowsSent.at(-1)).toEqual({ closesFrom: '2026-10-19T04:00:00.000Z', closesBefore: '2026-10-26T04:00:00.000Z' }),
    )

    await actor.keyboard(`{${back}}`)

    expect(closesFilter().getByRole('radio', { name: 'Today' })).toBeChecked()

    // Past the first option, the choice wraps round to the last.
    await actor.keyboard(`{${back}}{${back}}`)

    expect(closesFilter().getByRole('radio', { name: 'This month' })).toBeChecked()
  })

  // An arrow with Alt, Ctrl or Cmd held belongs to the browser or the screen
  // reader, not to the group: Alt+Left is the browser's Back.
  it('leaves an arrow with Alt, Ctrl or Cmd held to the browser', async () => {
    pinClock('2026-10-22T02:30:00Z')
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will SMU win SUNIG?')
    const thisWeek = closesFilter().getByRole('radio', { name: 'This week' })
    await actor.click(thisWeek)

    await actor.keyboard('{Alt>}{ArrowLeft}{/Alt}')
    expect(thisWeek).toBeChecked()
    await actor.keyboard('{Control>}{ArrowLeft}{/Control}')
    expect(thisWeek).toBeChecked()
    await actor.keyboard('{Meta>}{ArrowLeft}{/Meta}')
    expect(thisWeek).toBeChecked()
  })

  // One tab stop for the whole group, at the checked pill: Tab from the pill
  // before the group lands on it, and the next Tab leaves the group.
  it('reaches only the checked pill with Tab, as a radio group does', async () => {
    pinClock('2026-10-22T02:30:00Z')
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will SMU win SUNIG?')

    await actor.click(closesFilter().getByRole('radio', { name: 'Today' }))
    screen.getByRole('radio', { name: 'Resolved' }).focus()
    await actor.tab()
    expect(closesFilter().getByRole('radio', { name: 'Today' })).toHaveFocus()
    await actor.tab()
    expect(screen.getByRole('button', { name: 'Clear closing filter' })).toHaveFocus()
  })

  // The window belongs to the list, not to the click: a list started after
  // midnight, here by a new search, asks for the new day.
  it("asks for the new day's window when a search starts a new list after midnight", async () => {
    pinClock('2026-10-20T03:50:00Z')
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will SMU win SUNIG?')

    await actor.click(closesFilter().getByRole('radio', { name: 'Today' }))
    await waitFor(() =>
      expect(windowsSent.at(-1)).toEqual({ closesFrom: '2026-10-19T04:00:00.000Z', closesBefore: '2026-10-20T04:00:00.000Z' }),
    )

    vi.setSystemTime(new Date('2026-10-20T04:05:00Z'))
    await actor.type(screen.getByRole('searchbox', { name: /search markets/i }), 'smu{Enter}')

    await waitFor(() =>
      expect(windowsSent.at(-1)).toEqual({ closesFrom: '2026-10-20T04:00:00.000Z', closesBefore: '2026-10-21T04:00:00.000Z' }),
    )
  })

  // #442 AC 1 for the third filter: a preset alone is cleared by its own
  // chip, and Clear all appears once a search or a status joins it.
  it('offers Clear all only when the preset is one of two filters', async () => {
    pinClock('2026-10-22T02:30:00Z')
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will SMU win SUNIG?')
    const clearAll = () => screen.queryByRole('button', { name: 'Clear all' })

    await actor.click(closesFilter().getByRole('radio', { name: 'Today' }))
    expect(screen.getByRole('button', { name: 'Clear closing filter' })).toBeInTheDocument()
    expect(clearAll()).not.toBeInTheDocument()

    await actor.click(screen.getByRole('radio', { name: 'Closed' }))
    expect(clearAll()).toBeInTheDocument()

    await actor.click(screen.getByRole('button', { name: 'Clear status filter' }))
    expect(clearAll()).not.toBeInTheDocument()

    await actor.type(screen.getByRole('searchbox', { name: /search markets/i }), 'smu{Enter}')
    expect(clearAll()).toBeInTheDocument()
  })

  it('clears the closing preset along with everything else on Clear all', async () => {
    pinClock('2026-10-22T02:30:00Z')
    const actor = userEvent.setup()
    renderPage()
    await screen.findByText('Will SMU win SUNIG?')

    await actor.click(closesFilter().getByRole('radio', { name: 'Today' }))
    await actor.type(screen.getByRole('searchbox', { name: /search markets/i }), 'smu{Enter}')
    await actor.click(screen.getByRole('button', { name: 'Clear all' }))

    expect(await screen.findByText('Will SMU win SUNIG?')).toBeInTheDocument()
    expect(windowsSent.at(-1)).toEqual({ closesFrom: null, closesBefore: null })
    expect(closesFilter().getByRole('radio', { name: 'Any time' })).toBeChecked()
  })
})
