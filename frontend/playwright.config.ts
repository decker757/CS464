import { defineConfig, devices } from '@playwright/test'

// [F-12] #199. The real frontend in a real browser against the real backend.
// The backend stack must already be running (`docker compose up -d --build
// --wait` from the repo root); this starts only the frontend, built with the
// same VITE_* addresses the tests use.
const baseURL = process.env.E2E_BASE_URL ?? 'http://localhost:5173'
const port = new URL(baseURL).port

export default defineConfig({
  testDir: './e2e',
  // `.e2e.ts`, not `.spec.ts`, so vitest's default pattern never picks these up.
  testMatch: '**/*.e2e.ts',
  // [F-12] #341. The setup forgets the last run's pages; the teardown fails the
  // run if a route in App.tsx was never opened. The teardown is a no-op unless
  // `CI` is set, so running one file locally does not fail on the rest.
  globalSetup: './e2e/support/globalSetup.ts',
  globalTeardown: './e2e/support/globalTeardown.ts',
  // No retries: a test that passes on its second try is a flaky test, and a
  // retry would hide it.
  retries: 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL,
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: `npm run build && npm run preview -- --port ${port} --strictPort`,
    url: baseURL,
    // Locally, a `npm run dev` already on this port is used as it is.
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
})
