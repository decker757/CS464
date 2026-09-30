# Frontend code conventions

React 19, TypeScript (strict), Vite, Tailwind v4, React Router 7, axios.
Tests: Vitest, Testing Library, MSW. Lint: oxlint.

```bash
npm run dev
npm run test:run      # CI runs this,
npm run lint          # this,
npx tsc --noEmit      # this (and `npx tsc --noEmit -p e2e` for the e2e tests),
npm run build         # and this. Run all four before pushing.
```

The backend contract is the OpenAPI page at `/docs` on each service and
`docs/api/`. If the UI needs a field the API does not return, that is a
backend ticket, not a workaround here.

These rules apply to code you write or substantially rewrite. Do not restyle
or rename untouched files in a feature PR; that belongs in its own PR.

## Rules for both sides

The root `CLAUDE.md` ("Code style, tests and review") sets four rules that
apply here too. It has the detail; this is the short version:

- **Search before you write.** Reuse or extend what already exists. A PR that
  would add a second copy of some logic extracts it instead.
- **Readable beats clever.** Use the framework's own tools, but when that makes
  a line hard to read, write the plain version.
- **Tests earn their place.** Every test traces to an acceptance criterion, a
  Definition of Done item, a written invariant or a real bug. A test that would
  still pass with the feature broken gets deleted.
- **Review against the ticket.** Run `/pr-review` before asking a person to
  review, and nobody reviews their own work.

## Where things go

```
src/api/          the axios instance, one file per backend service
                  (authApi.ts, marketApi.ts, ledgerApi.ts) of typed request
                  functions, and error parsing (errors.ts)
src/components/ui/  the shared building blocks: Button, TextInput, Field,
                  Card, Badge, Logo, icons. Look here before styling anything
src/components/layout/  page frames used by more than one page: TopBar,
                  AppLayout. A frame for one feature lives with that
                  feature, like components/auth/AuthLayout
src/components/   reusable pieces; feature-specific ones in a subfolder
                  (components/auth/, components/landing/)
src/pages/        one per route; composes components and owns page state
src/hooks/        custom hooks (useX)
src/context/      React context providers
src/utils/        pure functions with no React in them
src/test/         MSW server and handlers
```

Pages and components never write a URL string. They call a typed function
from `src/api/<service>Api.ts`, so every endpoint is written down once.

Tests sit next to the file they test: `LoginPage.tsx` → `LoginPage.test.tsx`.

## Naming

- Components: `PascalCase`, one per file, file named after it.
- Props type: `<Component>Props`.
- Hooks: `useThing`. Handlers inside a component: `handleThing`. Props that
  take a handler: `onThing`.
- Booleans read as yes/no questions: `isLoading`, `hasError`, `showPassword`.
- Functions are verbs that say the outcome: `validateRegister`,
  `parseApiError`, `formatCredits`.
- Never: `data`, `info`, `temp`, `res2`, `e2`, `handleStuff`, `Helper`.

## File layout, top to bottom

1. Imports.
2. Types and interfaces.
3. Constants.
4. Helper functions that do not use props or state — outside and above the
   component, so they are not recreated each render and can be tested alone.
5. The component. Inside it: hooks, then derived values, then handlers, then
   the returned JSX.

If a helper is used by two files, it moves to `src/utils/` (pure) or
`src/hooks/` (uses React).

## Styling

- Use Tailwind classes. Colours come from the `@theme` tokens in
  `src/index.css` — `bg-smu-navy`, `text-smu-gold`, `bg-smu-cream`. Add a new
  colour there, not as a hex string in a component.
- Hover and focus use `hover:` and `focus:` classes, never
  `onMouseEnter`/`onMouseLeave` handlers that edit `style`.
- `style={{}}` only for values computed at runtime, like a bar width from a
  price.
- The same set of classes in two places means a component is missing. Use the
  one in `src/components/ui/` or add one there; do not copy the classes.

## Data and errors

- Turning a failed request into a message lives in one function in
  `src/api/errors.ts`. Match on `error.code` from the backend, never on the
  message text. Pages call that function; they do not parse axios errors.
- Money arrives as decimal **strings**. Never `parseFloat` a balance or cost
  to do arithmetic with it; display it through one formatting helper.
- The server is the authority. Show the cost the preview endpoint returns and
  the balance the ledger returns; do not recompute either in the browser.
- Auth rides in cookies (ADR 0002). Never put a token in `localStorage`, a
  URL, or a WebSocket query string.

## Components

- One job each. Past ~150 lines, or holding two unrelated groups of state,
  it is two components.
- Every input has a `<label>`; every error message has `role="alert"`. This is
  accessibility and also what the tests find elements by.

## Tests

What counts as a test worth keeping is in the root `CLAUDE.md` under
**Tests earn their place**. Frontend specifics:

- Test what the user sees and does: find elements by role, label or text,
  act with `userEvent`, and fake the network with MSW handlers.
- Do not mock axios, internal modules or child components, and do not assert
  on class names, inline styles or component state. Those tests break on a
  restyle and pass when the feature is broken.
- Each page gets its main flow and at least one server-error case asserting
  the message the user sees.

## End-to-end tests

`e2e/` holds Playwright tests that drive the real frontend in a real browser
against the real backend ([F-12] #199). CI runs them on every PR that touches
`frontend/`, `backend/`, `sql/`, `docker-compose.yml` or `.env.example`
(`.github/workflows/e2e.yml`). To run them
locally, start the stack first:

```bash
docker compose up -d --build --wait     # from the repo root
npm run e2e                             # builds and serves the frontend itself
```

They exist for the seams the other tests cannot see: a unit test here fakes
the backend with MSW, and a backend test has no browser, so a field renamed on
one side passes both. Keep them few and about journeys across services.
Everything else belongs in a unit test.

- Set data up through the API (`e2e/support/backend.ts`), and click through
  only the thing under test. Admins are promoted with `docker compose exec`,
  the way a first admin is made by hand.
- Every name carries a per-run suffix, so tests run in parallel and against a
  database that already has data in it. Never assert a total or a count.
- They run against whatever stack answers on the `VITE_*` addresses, so they
  also run beside your dev stack: another compose project on other ports,
  with the same variables pointed at it, `E2E_BASE_URL` for the frontend,
  and `COMPOSE_PROJECT_NAME` so promotion reaches that project's database.

## Before calling it done

Run the four commands above, then `/pr-review` on your branch, and fix what it
finds before asking a person.
