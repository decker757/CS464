import { expect, test } from './support/fixtures'
import { publishMarket, registerAdmin, registerTrader, uniqueName } from './support/backend'
import { logIn, marketCard, setQueryParams } from './support/browser'

// [X-1] #431 AC 2. Nothing but a browser can see this: jsdom has no layout
// and no scroll position, so a unit test cannot tell a list that came back
// where the trader left it from one that came back at the top. Measured
// before the fix: scrolled to 1,400px, opened a card, pressed Back, landed at
// 0 — and the list is fifty cards, so browsing is the whole loop.
//
// Enough markets to make one column taller than the window, published under
// one token so every list read is this run's own markets however much other
// runs have left in the database. That matters twice over here: the search
// box is page state and does not survive the remount a Back causes, so
// scoping the request itself is the only way the list is the same list, and
// the same height, on both sides of the Back.
const MARKETS = 5
// The card to open: far enough down that scrolling to it is a real offset,
// and far enough from the end that the offset is nowhere near the bottom of
// the page. At the bottom the browser clamps the scroll to whatever the page
// can reach, which would make this a test about the page's exact height.
const CARD_TO_OPEN = 2

test('coming back from a market returns the browse list to where it was', async ({ page }) => {
  const admin = await registerAdmin('sr_admin')
  const run = uniqueName('scroll')
  const questions = Array.from({ length: MARKETS }, (_, index) => `Will ${run} hold position ${index}?`)
  // Hours apart, so the order is this list's own and nothing closes mid-test.
  for (const [index, question] of questions.entries()) {
    await publishMarket(admin, question, undefined, { closesInHours: 24 + index })
  }
  const trader = await registerTrader('sr_trader')

  await setQueryParams(page, '/public/markets', { q: run })
  // One column and a short window, so five cards are taller than what is on
  // screen and there is something to scroll in the first place. Five rather
  // than more: every market here is a market every other test's browse list
  // then pages through, and this is a shared database.
  await page.setViewportSize({ width: 500, height: 400 })
  await logIn(page, trader)

  // Every card's prices in before anything is scrolled: a card grows by a
  // line when its snapshot lands, and Chrome's scroll anchoring moves the
  // offset to match when the card that grew is above the fold. Scrolling
  // while that is still going on measures a position the page has not
  // finished choosing, which is the test's problem and not the page's.
  for (const question of questions) {
    await expect(marketCard(page, question).getByText(/no trades yet/i)).toBeVisible()
  }

  const target = marketCard(page, questions[CARD_TO_OPEN])
  await target.scrollIntoViewIfNeeded()
  const leftAt = await page.evaluate(() => Math.round(window.scrollY))
  // The test is only about something if the list really was scrolled.
  expect(leftAt).toBeGreaterThan(300)

  // The card is in view, so this click scrolls nothing on its way.
  await target.click()
  await expect(page).toHaveURL(/\/markets\/[\w-]+$/)
  // A screen the trader has not been on starts at the top, the way a page
  // load does, rather than at the offset the list was left at.
  await expect.poll(() => page.evaluate(() => Math.round(window.scrollY))).toBe(0)

  await page.goBack()

  await expect(marketCard(page, questions[0])).toBeVisible()
  // Every card's prices in again, as before the scroll: the list mounts afresh
  // on the way back and re-reads them, and a card is a line shorter until its
  // snapshot lands (#502).
  //
  // This wait is a guess at #502's cause, not a confirmed fix. The flake never
  // reproduced locally: 20 runs, and 20 more each under four workers, under
  // eight busy cores and with the batch snapshot delayed. And in the CI
  // failure the viewport check below retried for its whole five seconds with
  // the card at ratio 0. A restore that was only late would have passed inside
  // that time, so the page may be restoring to the wrong offset instead.
  for (const question of questions) {
    await expect(marketCard(page, question).getByText(/no trades yet/i)).toBeVisible()
  }

  // The card the trader was looking at is on screen again, and the list is
  // nowhere near the top — where every Back used to land, a screen and a half
  // above this card. The offset rather than the exact pixel: the restore puts
  // back what it recorded, and Chrome's scroll anchoring then nudges it by a
  // few pixels as the last cards fill in, so the promise worth pinning is the
  // card being back in view.
  await expect(target).toBeInViewport({ ratio: 0.5 })
  expect(await page.evaluate(() => Math.round(window.scrollY))).toBeGreaterThan(300)
})
