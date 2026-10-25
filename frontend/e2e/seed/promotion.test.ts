import { describe, expect, it } from 'vitest'
import { refuseUnlessAdmin } from './promotion'

// #454's review: the seed's HTTP calls and its SQL step can reach two different
// stacks (the URLs name one, `docker compose exec` the current compose
// project). Then the promotion lands in the wrong database and the "admin"
// logs in as a trader. The login token's `role` claim says which.

function tokenWithClaims(claims: Record<string, unknown>): string {
  const header = Buffer.from(JSON.stringify({ alg: 'HS256', typ: 'JWT' })).toString('base64url')
  const payload = Buffer.from(JSON.stringify(claims)).toString('base64url')
  return `${header}.${payload}.signature`
}

describe('refuseUnlessAdmin', () => {
  it('accepts a token whose role is admin', () => {
    const token = tokenWithClaims({ sub: 'u1', username: 'admin_rachel', role: 'admin' })

    expect(() => refuseUnlessAdmin(token, 'cs464-demo')).not.toThrow()
  })

  it('refuses a token that is still a trader, naming the compose project the SQL step reached', () => {
    const token = tokenWithClaims({ sub: 'u1', username: 'admin_rachel', role: 'trader' })

    expect(() => refuseUnlessAdmin(token, 'cs464-demo')).toThrow('cs464-demo')
  })
})
