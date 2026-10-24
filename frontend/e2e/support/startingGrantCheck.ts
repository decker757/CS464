import { startingGrant } from './backend'

// #373. Reads the grant once before any test runs, so a STARTING_CREDITS too
// small for the suite stops the run with startingGrant's message, rather than
// leaving the tests to fail one by one on balances that read like ledger bugs.

export default async function checkStartingGrant(): Promise<void> {
  await startingGrant()
}
