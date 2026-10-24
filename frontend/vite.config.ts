import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    // A process per worker, as vitest defaults to today. MarketsPage's
    // closing-preset tests set TZ at run time, which is relied on only in a
    // process of its own, not in a worker thread (#442). Pinned so a change of
    // default, or a switch to `threads`, is a decision rather than a surprise;
    // the tests also check the zone took before they run.
    pool: 'forks',
    // [F-12] #342. `npm run test:coverage`, and the figure CI puts on the run
    // summary. It reports and never gates — nothing fails on a low number.
    coverage: {
      provider: 'v8',
      reporter: ['text-summary', 'json-summary'],
      // The app, not the scaffolding: `main.tsx` only mounts React, `src/test`
      // is the MSW server the tests fake the backend with, and a test is not
      // the thing being measured. The e2e tests count towards none of this —
      // they drive a built bundle in a real browser.
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/main.tsx', 'src/test/**', 'src/**/*.test.{ts,tsx}', 'src/**/*.d.ts'],
    },
  },
})
