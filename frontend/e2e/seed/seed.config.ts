import { defineConfig } from '@playwright/test'

// #454. `npm run seed:demo`: runs the demo seed under the runtime the e2e
// helpers it reuses already run under. No browser, no web server and none of
// the suite's global setup: the seed only calls the backend.
export default defineConfig({
  testDir: '.',
  testMatch: 'seedDemo.seed.ts',
  workers: 1,
  retries: 0,
  reporter: 'list',
})
