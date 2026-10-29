import { expect, test } from './support/fixtures'
import { buy, closeEarly, proposeOutcome, publishMarket, registerAdmin, registerTrader, saveDraft, uniqueName } from './support/backend'
import { boxOf, documentWidth, loadUntilShown, logIn, marketCard, overflowingScreens, selectTab, type Screen } from './support/browser'

// [FE] #438. Every signed-in screen used to be two to three times the width
// of a phone: the navbar was one row that never wrapped, and the create form
// a fixed two-column grid. A unit test cannot see this — jsdom lays nothing
// out — so the browser is the only place the width can be measured.

const PHONE = { width: 390, height: 844 }
const DESKTOP = { width: 1440, height: 900 }

test('no guest or trader screen scrolls sideways on a phone or on a desktop', async ({ page }) => {
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

  // The guest pages share the bar's frame, and its padding is narrower on a
  // phone now, so they are measured too, before signing in.
  const guestScreens: Screen[] = [
    { path: '/', heading: 'Trade on what you think happens next.' },
    { path: '/login', heading: 'Log in to your account' },
    { path: '/register', heading: 'Create your account' },
  ]
  expect(await overflowingScreens(page, guestScreens, PHONE)).toEqual([])
  expect(await overflowingScreens(page, guestScreens, DESKTOP)).toEqual([])

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

// [FE] #516. The test above failed now and then at 1440px, on these two lists
// alone and by one to four pixels, depending on the run's random username: the
// account button drew the whole name, so a wide one pushed the bar past its
// frame, and the bold link to the page that is open tipped it over. So the
// widest of each thing these lists draw is set up here, rather than left to
// whatever else is in the database: the longest username auth allows, in its
// widest letter, a question with no space to wrap at, and a winner with none.
// The browse list is measured too, because this market stays in the database
// for every later run, and a trader's card drew its question unwrapped. So are
// the user list and the user's own page, found by searching for this admin
// rather than left to whoever else is newest: the email is the username and
// more, and both screens drew it unwrapped.
test('no list scrolls sideways with the longest username, an unbroken question or an unbroken outcome', async ({ page }) => {
  // uniqueName adds up to twelve characters, which makes 32, auth's limit.
  const admin = await registerAdmin('W'.repeat(20))
  const question = `${'W'.repeat(400)}${uniqueName('q')}`
  // The longest label market_service accepts.
  const winner = 'W'.repeat(120)
  const market = await publishMarket(admin, question, [winner, 'No'])
  await closeEarly(admin, market.id)
  await proposeOutcome(admin, market, winner)
  // The same again, traded, because a priced card draws its two sides above
  // a split bar rather than as chips (#441), and that row has its own way to
  // be too wide. Priced against the long label, so its side is the short end
  // of the bar: 200 No shares at b = 100 leave the winner 11.9%.
  const pricedQuestion = `${'W'.repeat(400)}${uniqueName('q_priced')}`
  const priced = await publishMarket(admin, pricedQuestion, [winner, 'No'])
  await buy(await registerTrader('frame_skew'), priced, 'No', '200')
  await closeEarly(admin, priced.id)
  await proposeOutcome(admin, priced, winner)

  await logIn(page, admin)

  for (const viewport of [PHONE, DESKTOP]) {
    await page.setViewportSize(viewport)
    await page.goto('/admin/markets')
    // Its own tab, so the paged list reaches it in a page or two.
    await selectTab(page, /^pending/i)
    await expect(await loadUntilShown(page, question)).toBeVisible()
    const adminList = await documentWidth(page)
    expect(adminList.scrollWidth, `/admin/markets at ${viewport.width}px`).toBeLessThanOrEqual(adminList.clientWidth)

    expect(await overflowingScreens(page, [{ path: '/admin/proposals', heading: 'Proposals Awaiting Decision' }], viewport)).toEqual([])
    // The list just measured had this market on it.
    await expect(page.getByText(question)).toBeVisible()

    await page.goto('/markets')
    // Stopped markets are listed most recently stopped first, so this one,
    // closed a moment ago, leads its filter.
    await page.getByRole('radio', { name: 'Resolving', exact: true }).click()
    await expect(page.getByText(question)).toBeVisible()
    await expect(marketCard(page, pricedQuestion).getByRole('img', { name: /^W+ 11\.9%, No 88\.1%$/ })).toBeVisible()
    const browseList = await documentWidth(page)
    expect(browseList.scrollWidth, `/markets at ${viewport.width}px`).toBeLessThanOrEqual(browseList.clientWidth)

    await page.goto('/admin/users')
    await page.getByLabel('Search by username or email').fill(admin.username)
    await page.getByRole('button', { name: 'Search' }).click()
    // One card, and it is this admin's, so the list measured is the searched
    // one and it draws this email. Before the search answers, there are more.
    const userCards = page.getByRole('link').filter({ hasText: /@example\.com/i })
    await expect(userCards).toHaveCount(1)
    const userCard = userCards.filter({ has: page.getByText(admin.username, { exact: true }) })
    await expect(userCard).toBeVisible()
    const userList = await documentWidth(page)
    expect(userList.scrollWidth, `/admin/users at ${viewport.width}px`).toBeLessThanOrEqual(userList.clientWidth)

    // Opened from the card, since the email rides in on the link: a page
    // opened by its address alone has no email to draw.
    await userCard.click()
    await expect(page.getByRole('heading', { level: 1, name: `${admin.username}'s History`, exact: true })).toBeVisible()
    await expect(page.getByText(/@example\.com$/i)).toBeVisible()
    await expect(page.getByText(/^Loading\b/)).toHaveCount(0)
    const userPage = await documentWidth(page)
    expect(userPage.scrollWidth, `/admin/users/${admin.id} at ${viewport.width}px`).toBeLessThanOrEqual(userPage.clientWidth)
  }
})

// [FE] #429. On an open market the outcome prices are the control that picks
// what the panel trades, and a market may have ten outcomes of up to 120
// characters each. The blocks wrap onto rows rather than drawing ten columns,
// and a label breaks inside its block: the worst case is set up here rather
// than left to whatever the database holds.
test('an open market with ten unbroken outcomes does not scroll sideways', async ({ page }) => {
  const admin = await registerAdmin('frame_outcomes')
  const trader = await registerTrader('frame_chooser')
  // The longest label market_service accepts, ten times over, and unique.
  const labels = Array.from({ length: 10 }, (_, index) => `${'W'.repeat(119)}${index}`)
  const market = await publishMarket(admin, `Do ten outcomes fit ${uniqueName('ten')}?`, labels)
  const screens: Screen[] = [{ path: `/markets/${market.id}`, heading: market.question }]

  await logIn(page, trader)

  expect(await overflowingScreens(page, screens, PHONE)).toEqual([])
  expect(await overflowingScreens(page, screens, DESKTOP)).toEqual([])
  // Measured with the outcomes drawn as choices, which is the open market's
  // layout, rather than as the figures a stopped one shows.
  await expect(page.getByRole('radio')).toHaveCount(10)
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

  // Visible first, or "hidden" would also pass on a page that had not drawn
  // the bar at all.
  await expect(menuButton).toBeVisible()
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
