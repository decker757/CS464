import { expect, test } from './support/fixtures'
import { buy, closeEarly, proposeOutcome, publishMarket, registerAdmin, registerTrader, saveDraft, uniqueName } from './support/backend'
import { boxOf, logIn, overflowingScreens, type Screen } from './support/browser'

// [FE] #438. Every signed-in screen used to be two to three times the width
// of a phone: the navbar was one row that never wrapped, and the create form
// a fixed two-column grid. A unit test cannot see this — jsdom lays nothing
// out — so the browser is the only place the width can be measured.

const PHONE = { width: 390, height: 844 }
const DESKTOP = { width: 1440, height: 900 }

test('no trader screen scrolls sideways on a phone or on a desktop', async ({ page }) => {
  const admin = await registerAdmin('frame_owner')
  const trader = await registerTrader('frame_trader')
  const market = await publishMarket(admin, `Does the frame fit ${uniqueName('phone')}?`)
  // A position and a trade, so the portfolio and the history draw their
  // tables rather than their empty states.
  await buy(trader, market, 'Yes', '2')
  const screens: Screen[] = [
    { path: '/markets', heading: 'Markets' },
    { path: `/markets/${market.id}`, heading: market.question },
    { path: '/portfolio', heading: 'Portfolio' },
    { path: '/history', heading: 'Trade History' },
    { path: '/leaderboard', heading: 'Leaderboard' },
  ]

  await logIn(page, trader)

  expect(await overflowingScreens(page, screens, PHONE)).toEqual([])
  expect(await overflowingScreens(page, screens, DESKTOP)).toEqual([])
})

test('no administrator screen scrolls sideways on a phone or on a desktop', async ({ page }) => {
  // The administrator's bar is the widest there is: seven links, a divider
  // and the New Market button on top of the trader's.
  const admin = await registerAdmin('frame_admin')
  const trader = await registerTrader('frame_subject')
  const open = await publishMarket(admin, `Is the admin list narrow enough ${uniqueName('open')}?`)
  await buy(trader, open, 'Yes', '2')
  const draft = await saveDraft(admin, `Does the edit form fit ${uniqueName('draft')}?`)
  const closed = await publishMarket(admin, `Can a phone propose ${uniqueName('closed')}?`)
  await closeEarly(admin, closed.id)
  const proposed = await publishMarket(admin, `Can a phone review ${uniqueName('proposed')}?`)
  await closeEarly(admin, proposed.id)
  await proposeOutcome(admin, proposed, 'Yes')
  const screens: Screen[] = [
    { path: '/admin/markets', heading: 'All Markets' },
    { path: '/admin/markets/new', heading: 'New Market' },
    { path: `/admin/markets/${draft.id}/edit`, heading: 'Edit Market' },
    { path: `/admin/markets/${closed.id}/propose-outcome`, heading: 'Propose Outcome' },
    { path: '/admin/proposals', heading: 'Proposals Awaiting Decision' },
    { path: `/admin/markets/${proposed.id}/decide-outcome`, heading: 'Review Proposal' },
    { path: `/admin/markets/${open.id}/exposure`, heading: open.question },
    { path: `/admin/markets/${open.id}/price-history`, heading: open.question },
    { path: '/admin/users', heading: 'Users' },
    { path: `/admin/users/${trader.id}`, heading: `${trader.username}'s History` },
  ]

  await logIn(page, admin)

  expect(await overflowingScreens(page, screens, PHONE)).toEqual([])
  expect(await overflowingScreens(page, screens, DESKTOP)).toEqual([])
})

test('on a phone the navigation sits behind a menu button, and on a desktop it is in the bar', async ({ page }) => {
  // What jsdom cannot show the unit tests: that the links really are out of
  // sight until the button is pressed, and in sight without it at 1440.
  const admin = await registerAdmin('frame_menu')
  await page.setViewportSize(PHONE)
  await logIn(page, admin)
  const banner = page.getByRole('banner')
  const menuButton = banner.getByRole('button', { name: 'Menu', exact: true })
  const usersLink = banner.getByRole('link', { name: 'Users', exact: true })

  await expect(usersLink).toBeHidden()
  await menuButton.click()
  await expect(menuButton).toHaveAttribute('aria-expanded', 'true')
  await usersLink.click()

  await expect(page.getByRole('heading', { level: 1, name: 'Users', exact: true })).toBeVisible()
  await expect(menuButton).toHaveAttribute('aria-expanded', 'false')
  await expect(usersLink).toBeHidden()

  await page.setViewportSize(DESKTOP)
  await expect(menuButton).toBeHidden()
  await expect(usersLink).toBeVisible()
})

test('the create form is one column on a phone and two on a desktop', async ({ page }) => {
  // Not width alone: two date inputs squeezed side by side can fit 390px and
  // still be unusable, so this checks where the second field sits.
  const admin = await registerAdmin('frame_form')
  await page.setViewportSize(PHONE)
  await logIn(page, admin)
  await page.goto('/admin/markets/new')
  await expect(page.getByRole('heading', { level: 1, name: 'New Market', exact: true })).toBeVisible()
  const closesAt = page.getByLabel('Closes at *', { exact: true })
  const resolvesBy = page.getByLabel('Resolves by *', { exact: true })

  const phoneCloses = await boxOf(closesAt)
  const phoneResolves = await boxOf(resolvesBy)
  expect(phoneResolves.y).toBeGreaterThanOrEqual(phoneCloses.y + phoneCloses.height)

  await page.setViewportSize(DESKTOP)
  const desktopCloses = await boxOf(closesAt)
  const desktopResolves = await boxOf(resolvesBy)
  expect(desktopResolves.y).toBe(desktopCloses.y)
  expect(desktopResolves.x).toBeGreaterThan(desktopCloses.x + desktopCloses.width)
})
