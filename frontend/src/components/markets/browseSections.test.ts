import { describe, expect, it } from 'vitest'
import type { MarketSnapshot } from '../../api/ledgerApi'
import type { PublicMarketSummary } from '../../api/marketApi'
import { browseSectionsOf, featuredMarketOf } from './browseSections'

const NOW = new Date('2026-10-25T12:00:00Z')
// Well past the Closing soon window, so a market using it lands in a band on
// its own merits. Every published market has a close time — `close_time` is
// not nullable on `PublicMarketSummary` — and a market meant to be tradeable
// needs one in the future.
const FAR_OFF = '2026-11-25T12:00:00Z'

function market(id: string, closeTime: string, status: 'open' | 'closed' = 'open'): PublicMarketSummary {
  return {
    id,
    status,
    question: `Will ${id} happen?`,
    close_time: closeTime,
    outcomes: [
      { id: `${id}-0`, position: 0, label: 'Yes' },
      { id: `${id}-1`, position: 1, label: 'No' },
    ],
  }
}

function snapshot(
  marketId: string,
  { traded = '0.0000', price = '0.5000', weekAgo }: { traded?: string; price?: string; weekAgo?: string | null } = {},
): MarketSnapshot {
  return {
    market_id: marketId,
    state_version: 3,
    prices: [
      { outcome_id: `${marketId}-0`, position: 0, price },
      { outcome_id: `${marketId}-1`, position: 1, price: '0.5000' },
    ],
    occurred_at: '2026-10-25T11:00:00Z',
    prices_7d_ago:
      weekAgo === undefined || weekAgo === null
        ? null
        : [
            { outcome_id: `${marketId}-0`, position: 0, price: weekAgo },
            { outcome_id: `${marketId}-1`, position: 1, price: '0.5000' },
          ],
    credits_traded: traded,
  }
}

// The prices, and the two figures only the batch carries — which the hook
// keeps apart because a `price` frame carries neither (ledger-service.md).
function readsOf(...entries: MarketSnapshot[]) {
  return {
    snapshots: new Map(entries.map(entry => [entry.market_id, entry])),
    figures: new Map(
      entries.map(entry => [
        entry.market_id,
        { credits_traded: entry.credits_traded, prices_7d_ago: entry.prices_7d_ago },
      ]),
    ),
  }
}

const sectionNamed = (sections: ReturnType<typeof browseSectionsOf>, id: string) =>
  sections.find(section => section.id === id)

describe('browseSectionsOf', () => {
  // #448 AC 3: a section with nothing in it disappears rather than rendering
  // an empty row. On a young database most will be empty, which is the normal
  // case here and not an edge case.
  it('leaves out every section it has nothing for', () => {
    // Closes in a month, never traded: in no band.
    const quiet = [market('m1', '2026-11-25T12:00:00Z')]
    const reads = readsOf(snapshot('m1'))

    const sections = browseSectionsOf(quiet, reads.snapshots, reads.figures, NOW)

    expect(sections).toEqual([])
  })

  // AC 2: each section states its rule, so "why is this here" is never a
  // mystery. The rule is rendered under the heading.
  it('states a rule for every section it returns', () => {
    const closing = [market('m1', '2026-10-25T20:00:00Z')]
    const reads = readsOf(snapshot('m1'))

    const sections = browseSectionsOf(closing, reads.snapshots, reads.figures, NOW)

    expect(sections).toHaveLength(1)
    expect(sections[0].title).toBe('Closing soon')
    expect(sections[0].rule).toBeTruthy()
  })

  it('puts a market closing within the day in Closing soon, soonest first', () => {
    const markets = [
      market('later', '2026-10-26T06:00:00Z'),
      market('sooner', '2026-10-25T18:00:00Z'),
      market('next_month', '2026-11-25T12:00:00Z'),
    ]

    const sections = browseSectionsOf(markets, new Map(), new Map(), NOW)

    expect(sectionNamed(sections, 'closing_soon')?.markets.map(m => m.id)).toEqual(['sooner', 'later'])
  })

  // The window is a day, edge included: a market closing in exactly 24 hours
  // is closing soon, and one closing an hour later is not.
  it('bands a market closing in exactly 24 hours, and not one closing in 25', () => {
    const markets = [market('edge', '2026-10-26T12:00:00Z'), market('past_edge', '2026-10-26T13:00:00Z')]

    const sections = browseSectionsOf(markets, new Map(), new Map(), NOW)

    expect(sectionNamed(sections, 'closing_soon')?.markets.map(m => m.id)).toEqual(['edge'])
  })

  // A band is a shortcut, not a second copy of the list: three cards at most,
  // however many qualify.
  it('holds at most three markets in a band', () => {
    const markets = ['a', 'b', 'c', 'd'].map(id => market(id, '2026-10-25T18:00:00Z'))

    const sections = browseSectionsOf(markets, new Map(), new Map(), NOW)

    expect(sectionNamed(sections, 'closing_soon')?.markets.map(m => m.id)).toEqual(['a', 'b', 'c'])
  })

  // #447 exists so "trending" is a real figure rather than a confident name on
  // an arbitrary order. A market nobody has traded is not trending at zero.
  it('ranks Trending by credits traded and leaves out an untraded market', () => {
    const markets = [market('busy', FAR_OFF), market('quiet', FAR_OFF), market('untouched', FAR_OFF)]
    const reads = readsOf(
      snapshot('busy', { traded: '1200.0000' }),
      snapshot('quiet', { traded: '30.0000' }),
      snapshot('untouched', { traded: '0.0000' }),
    )

    const sections = browseSectionsOf(markets, reads.snapshots, reads.figures, NOW)

    expect(sectionNamed(sections, 'trending')?.markets.map(m => m.id)).toEqual(['busy', 'quiet'])
  })

  // Credits are decimal strings and are never compared as floats or as text:
  // "9.0000" is more than "10.0000" by string order, and less by value.
  it('ranks Trending by value, not by string order', () => {
    const markets = [market('nine', FAR_OFF), market('ten', FAR_OFF)]
    const reads = readsOf(
      snapshot('nine', { traded: '9.0000' }),
      snapshot('ten', { traded: '10.0000' }),
    )

    const sections = browseSectionsOf(markets, reads.snapshots, reads.figures, NOW)

    expect(sectionNamed(sections, 'trending')?.markets.map(m => m.id)).toEqual(['ten', 'nine'])
  })

  // A market that fell thirty points is as interesting as one that rose, so
  // the band ranks the size of the move and not its direction.
  it('ranks Biggest movers by the size of the move, either way', () => {
    const markets = [market('rose', FAR_OFF), market('fell', FAR_OFF), market('crept', FAR_OFF)]
    const reads = readsOf(
      snapshot('rose', { traded: '1.0000', price: '0.6000', weekAgo: '0.5000' }),
      snapshot('fell', { traded: '1.0000', price: '0.2000', weekAgo: '0.5000' }),
      snapshot('crept', { traded: '1.0000', price: '0.5100', weekAgo: '0.5000' }),
    )

    const sections = browseSectionsOf(markets, reads.snapshots, reads.figures, NOW)

    // fell moved 0.30, rose 0.10, crept 0.01.
    expect(sectionNamed(sections, 'movers')?.markets.map(m => m.id)).toEqual(['fell', 'rose', 'crept'])
  })

  // A market's move is its largest outcome's move. Only on a binary book is
  // the second outcome the first one's mirror: on three outcomes the first can
  // sit still while the other two trade places.
  it('measures a move on every outcome, not only the first', () => {
    const threeWay: PublicMarketSummary = {
      ...market('three_way', FAR_OFF),
      outcomes: [
        { id: 'three_way-0', position: 0, label: 'Home' },
        { id: 'three_way-1', position: 1, label: 'Draw' },
        { id: 'three_way-2', position: 2, label: 'Away' },
      ],
    }
    const prices = (home: string, draw: string, away: string) => [
      { outcome_id: 'three_way-0', position: 0, price: home },
      { outcome_id: 'three_way-1', position: 1, price: draw },
      { outcome_id: 'three_way-2', position: 2, price: away },
    ]
    const snapshots = new Map([['three_way', { ...snapshot('three_way'), prices: prices('0.3300', '0.6000', '0.0700') }]])
    const figures = new Map([['three_way', { credits_traded: '80.0000', prices_7d_ago: prices('0.3300', '0.3400', '0.3300') }]])

    const sections = browseSectionsOf([threeWay], snapshots, figures, NOW)

    expect(sectionNamed(sections, 'movers')?.markets.map(m => m.id)).toEqual(['three_way'])
  })

  // ledger-service.md: `prices_7d_ago` is null when there is no change to
  // measure — never traded, traded only within the week, or a price the ledger
  // could not rebuild. No delta is shown, so the market is in no movers band.
  it('leaves out a market with no week-old price to compare', () => {
    const markets = [market('fresh', FAR_OFF)]
    const reads = readsOf(snapshot('fresh', { traded: '50.0000', price: '0.8000', weekAgo: null }))

    const sections = browseSectionsOf(markets, reads.snapshots, reads.figures, NOW)

    expect(sectionNamed(sections, 'movers')).toBeUndefined()
  })

  // Equal to `prices` is a real change of zero, not missing data — and a
  // market that has not moved is not a mover.
  it('leaves out a market whose price has not changed over the week', () => {
    const markets = [market('flat', FAR_OFF)]
    const reads = readsOf(snapshot('flat', { traded: '50.0000', price: '0.5000', weekAgo: '0.5000' }))

    const sections = browseSectionsOf(markets, reads.snapshots, reads.figures, NOW)

    expect(sectionNamed(sections, 'movers')).toBeUndefined()
  })

  // A band of markets nobody can trade helps nobody. The clock decides, not
  // the status column (ADR 0011), which is why a market whose status still
  // reads open is judged on its close time.
  it('leaves out a market the clock has closed, whatever its status says', () => {
    const markets = [market('stopped', '2026-10-25T06:00:00Z')]
    const reads = readsOf(snapshot('stopped', { traded: '900.0000', price: '0.9000', weekAgo: '0.5000' }))

    const sections = browseSectionsOf(markets, reads.snapshots, reads.figures, NOW)

    expect(sections).toEqual([])
  })

  // A band is a shortcut into the list, not a partition of it (AC 5): a market
  // in a band is still in the list below, so the band must not consume it.
  it('leaves the markets it bands in the list it was given', () => {
    const markets = [market('m1', '2026-10-25T18:00:00Z')]
    const reads = readsOf(snapshot('m1'))

    const sections = browseSectionsOf(markets, reads.snapshots, reads.figures, NOW)

    expect(sectionNamed(sections, 'closing_soon')?.markets).toHaveLength(1)
    expect(markets).toHaveLength(1)
  })
})

describe('featuredMarketOf', () => {
  // #448: one featured market at the top. Volume is the only honest basis —
  // the one figure that says other people are here, which is what the slot is
  // for.
  it('features the busiest market the page has loaded', () => {
    const markets = [market('quiet', FAR_OFF), market('busy', FAR_OFF)]
    const reads = readsOf(
      snapshot('quiet', { traded: '30.0000' }),
      snapshot('busy', { traded: '1200.0000' }),
    )

    expect(featuredMarketOf(markets, reads.figures, NOW)?.market.id).toBe('busy')
  })

  // Promoting an untouched book is how the screen came to read as dead in the
  // first place. On a database with no trades there is simply no hero, the
  // same rule every band follows.
  it('features nothing when no market has been traded', () => {
    const markets = [market('untouched', FAR_OFF)]
    const reads = readsOf(snapshot('untouched', { traded: '0.0000' }))

    expect(featuredMarketOf(markets, reads.figures, NOW)).toBeNull()
  })

  // A hero nobody can trade is worse than no hero. The clock decides, not the
  // status column (ADR 0011).
  it('features nothing the clock has closed, whatever its status says', () => {
    const markets = [market('stopped', '2026-10-25T06:00:00Z')]
    const reads = readsOf(snapshot('stopped', { traded: '900.0000' }))

    expect(featuredMarketOf(markets, reads.figures, NOW)).toBeNull()
  })

  // It states why it was chosen, so a featured slot is never an unexplained
  // promotion.
  it('says why the market was featured', () => {
    const markets = [market('busy', FAR_OFF)]
    const reads = readsOf(snapshot('busy', { traded: '1200.0000' }))

    expect(featuredMarketOf(markets, reads.figures, NOW)?.rule).toBeTruthy()
  })

  // The hero would otherwise lead Trending as well and sit above the list
  // twice, which reads as a broken band rather than as emphasis.
  it('is left out of the bands when passed as the featured market', () => {
    const markets = [market('busy', FAR_OFF), market('next', FAR_OFF)]
    const reads = readsOf(
      snapshot('busy', { traded: '1200.0000' }),
      snapshot('next', { traded: '30.0000' }),
    )

    const sections = browseSectionsOf(markets, reads.snapshots, reads.figures, NOW, 'busy')

    expect(sectionNamed(sections, 'trending')?.markets.map(m => m.id)).toEqual(['next'])
  })
})
