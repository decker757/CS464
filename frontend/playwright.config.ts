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
