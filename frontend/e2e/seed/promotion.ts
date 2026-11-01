// #454's review. The seed's HTTP calls go to the URLs it was given, and its
// SQL step goes to whichever compose project `docker compose exec` picks. If
// those are two different stacks, the promotion lands in the wrong database
// and the "admin" logs in as a trader. The login token's own `role` claim
// says which, so the seed checks it before it publishes anything.

function roleClaimOf(accessToken: string): unknown {
  const payload = accessToken.split('.')[1] ?? ''
  try {
    return JSON.parse(Buffer.from(payload, 'base64url').toString('utf8')).role
  } catch {
    return undefined
  }
}

/**
 * Throws unless the token's role is admin, naming where the SQL step ran and
 * what to fix: a local compose project, or production's database (#609).
 */
export function refuseUnlessAdmin(accessToken: string, sqlRanIn: string, fix: string): void {
  if (roleClaimOf(accessToken) === 'admin') return
  throw new Error(
    `The promotion to admin did not reach the stack the seed is talking to: the SQL step ran in ${sqlRanIn}, ` +
      `and the account still logs in as a trader. ${fix}`,
  )
}
