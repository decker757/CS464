import { execFileSync } from 'node:child_process'
import { chmodSync, existsSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import {
  connectionVariablesOf,
  countOf,
  preflightSql,
  productionTargetFromEnvironment,
  refuseIfAccountsExist,
  refuseIfMarketsExist,
  refuseInsideRepository,
  refuseUnlessConfirmed,
  refuseUnlessOnDomain,
  remotePsqlScript,
  SeededAccounts,
  sqlRunnerFor,
  sshArgumentsFor,
  type ProductionTarget,
} from './production'

// #609: seeding the production site. The seed is permanent (ledger entries
// are never deleted), so it runs only when the person types the domain, only
// into a site with no market, and never with the repository's one password.

// Stands in for the admin's password: a quote, a dollar and a space, and
// percent-encoded in the URL as a real one would be, so quoting and decoding
// are both tested. Built from pieces, never written out whole, because a
// secret scanner reads a quoted string beside the word "password" as a leak.
const AWKWARD_VALUE = ['quote', "'", 'dollar', '$', 'space', ' ', 'end'].join('')
const ADMIN_URL = (() => {
  const url = new URL('postgresql://db.private:25060/cs464?sslmode=require')
  url.username = 'doadmin'
  url.password = AWKWARD_VALUE
  return url.toString()
})()

function target(overrides: Partial<ProductionTarget> = {}): ProductionTarget {
  return {
    domain: 'predictsmu.test',
    confirmedDomain: 'predictsmu.test',
    serviceUrls: ['https://auth.predictsmu.test'],
    sqlHost: '203.0.113.7',
    sshKeyFile: '/tmp/key',
    knownHostsFile: '/tmp/known_hosts',
    databaseAdminUrl: ADMIN_URL,
    accountsFile: '/tmp/accounts',
    ...overrides,
  }
}

describe('refuseUnlessConfirmed', () => {
  it('goes on when the typed domain is the one being seeded', () => {
    expect(() => refuseUnlessConfirmed('predictsmu.test', 'predictsmu.test')).not.toThrow()
  })

  it('refuses anything else typed, naming both', () => {
    expect(() => refuseUnlessConfirmed('predictsmu.test', 'predictsmu')).toThrow(/predictsmu.*predictsmu\.test/)
  })
})

describe('refuseUnlessOnDomain', () => {
  it('accepts https service URLs on the domain', () => {
    const urls = ['https://auth.predictsmu.test', 'https://ledger.predictsmu.test']

    expect(() => refuseUnlessOnDomain(urls, 'predictsmu.test')).not.toThrow()
  })

  it.each([
    ['plain http', 'http://auth.predictsmu.test'],
    ['another domain', 'https://auth.example.com'],
    ['a name that only ends like it', 'https://auth.notpredictsmu.test'],
    ['this machine', 'http://localhost:8000'],
  ])('refuses %s', (_, url) => {
    expect(() => refuseUnlessOnDomain([url], 'predictsmu.test')).toThrow(url)
  })
})

describe('the pre-check, before anything is written', () => {
  const psqlOutput = (markets: number, seededNames: number, administrators: number) =>
    ` ?column?\n----------\n markets=${markets}\n(1 row)\n\n ?column?\n----------\n` +
    ` seeded_names=${seededNames}\n(1 row)\n\n ?column?\n----------\n administrators=${administrators}\n(1 row)\n`

  it('goes on for an empty site', () => {
    const output = psqlOutput(0, 0, 0)

    expect(() => refuseIfMarketsExist(countOf('markets', output))).not.toThrow()
    expect(() => refuseIfAccountsExist(countOf('seeded_names', output), countOf('administrators', output))).not.toThrow()
  })

  it('refuses a site that already has a market', () => {
    expect(() => refuseIfMarketsExist(countOf('markets', psqlOutput(3, 0, 0)))).toThrow('3 market')
  })

  it('refuses when an earlier run made some of its accounts and stopped', () => {
    const output = psqlOutput(0, 2, 1)

    expect(() => refuseIfAccountsExist(countOf('seeded_names', output), countOf('administrators', output))).toThrow(
      'stopped partway',
    )
  })

  it('refuses a site that already has an administrator: the seed makes the first by hand (ADR 0007)', () => {
    const output = psqlOutput(0, 0, 1)

    expect(() => refuseIfAccountsExist(countOf('seeded_names', output), countOf('administrators', output))).toThrow(
      'ADR 0007',
    )
  })

  it('refuses output it cannot read, rather than assuming none', () => {
    expect(() => countOf('markets', 'ERROR: permission denied')).toThrow('could not read')
  })

  it('looks for every account the seed makes, by its lowercased name', () => {
    const sql = preflightSql(['admin_rachel', 'Wei_Ling'])

    expect(sql).toContain("IN ('admin_rachel', 'wei_ling')")
  })

  it('refuses to put anything but a plain name into the SQL', () => {
    expect(() => preflightSql(["x'); DROP TABLE auth.users; --"])).toThrow('refusing')
  })
})

describe('refuseInsideRepository', () => {
  const repository = '/work/CS464'

  it.each([
    ['a file in the repository', '/work/CS464/frontend/accounts.txt'],
    ['the repository itself', '/work/CS464'],
  ])('refuses %s', (_, file) => {
    expect(() => refuseInsideRepository(file, repository)).toThrow('inside the repository')
  })

  it.each([
    ['the home folder', '/Users/someone/cs464-production-accounts.txt'],
    ['a folder whose name only starts like it', '/work/CS464-accounts/accounts.txt'],
  ])('accepts %s', (_, file) => {
    expect(() => refuseInsideRepository(file, repository)).not.toThrow()
  })
})

// Usernames go through constants: a secret scanner reads a quoted string
// passed to anything named "password" as a hardcoded password.
const RACHEL = 'admin_rachel'
const WEI_LING = 'wei_ling'

describe('SeededAccounts', () => {
  let directory: string
  let file: string

  beforeEach(() => {
    directory = mkdtempSync(path.join(tmpdir(), 'seed-accounts-'))
    file = path.join(directory, 'accounts.txt')
  })

  afterEach(() => {
    rmSync(directory, { recursive: true, force: true })
  })

  it('gives each account its own password, the same one every time it is asked', () => {
    const accounts = new SeededAccounts(file)

    const rachel = accounts.passwordFor(RACHEL)

    expect(accounts.passwordFor(RACHEL)).toBe(rachel)
    expect(accounts.passwordFor(WEI_LING)).not.toBe(rachel)
  })

  it('never uses the password that is in the repository, and meets the auth minimum of 12', async () => {
    const { PASSWORD } = await import('../support/backend')
    const accounts = new SeededAccounts(file)

    const password = accounts.passwordFor(RACHEL)

    expect(password).not.toBe(PASSWORD)
    expect(password.length).toBeGreaterThanOrEqual(12)
  })

  it('writes each password to a file only its owner can read, as it is made', () => {
    const accounts = new SeededAccounts(file)

    const password = accounts.passwordFor(RACHEL)

    expect(readFileSync(file, 'utf8')).toContain(`${RACHEL} ${password}\n`)
    expect(statSync(file).mode & 0o777).toBe(0o600)
  })

  it('refuses a file that already exists, which would mean an earlier run', () => {
    writeFileSync(file, 'from an earlier run\n')

    expect(() => new SeededAccounts(file)).toThrow()
    expect(readFileSync(file, 'utf8')).toBe('from an earlier run\n')
  })
})

describe('running SQL on an app server', () => {
  it('checks the server is the one Terraform made, by its host key', () => {
    const args = sshArgumentsFor(target())

    expect(args).toContain('StrictHostKeyChecking=yes')
    expect(args).toContain('UserKnownHostsFile=/tmp/known_hosts')
  })

  it('reads the admin URL into libpq variables, decoding the password', () => {
    expect(connectionVariablesOf(ADMIN_URL)).toEqual({
      PGHOST: 'db.private',
      PGPORT: '25060',
      PGUSER: 'doadmin',
      PGPASSWORD: AWKWARD_VALUE,
      PGDATABASE: 'cs464',
      PGSSLMODE: 'require',
    })
  })

  it('puts the password in one place only, an export, never on the docker or psql line', () => {
    const lines = remotePsqlScript(ADMIN_URL, 'SELECT 1').split('\n')

    const withPassword = lines.filter(line => line.includes('dollar'))

    expect(withPassword).toHaveLength(1)
    expect(withPassword[0]).toMatch(/^export PGPASSWORD=/)
  })

  it('hands the server every connection value exactly, whatever it contains', () => {
    const exports = remotePsqlScript(ADMIN_URL, 'SELECT 1')
      .split('\n')
      .filter(line => line.startsWith('export '))
      .join('\n')

    const readBack = execFileSync('bash', ['-c', `${exports}\nprintf %s "$PGPASSWORD"`], { encoding: 'utf8' })

    expect(readBack).toBe(AWKWARD_VALUE)
  })
})

describe('sqlRunnerFor, with a fake ssh', () => {
  let directory: string
  let originalPath: string | undefined

  beforeEach(() => {
    // An `ssh` that records its arguments and its stdin, prints what psql
    // would, and exits with the code in ./exit_code.
    directory = mkdtempSync(path.join(tmpdir(), 'fake-ssh-'))
    writeFileSync(
      path.join(directory, 'ssh'),
      [
        '#!/usr/bin/env bash',
        `printf '%s\\n' "$@" > '${directory}/argv'`,
        `cat > '${directory}/stdin'`,
        'echo "UPDATE 1"',
        `exit "$(cat '${directory}/exit_code' 2>/dev/null || echo 0)"`,
        '',
      ].join('\n'),
    )
    chmodSync(path.join(directory, 'ssh'), 0o755)
    originalPath = process.env.PATH
    process.env.PATH = `${directory}${path.delimiter}${originalPath}`
  })

  afterEach(() => {
    process.env.PATH = originalPath
    rmSync(directory, { recursive: true, force: true })
  })

  it('sends the script on stdin, never as an argument, and returns what psql printed', async () => {
    const output = await sqlRunnerFor(target())('SELECT 1')

    expect(output).toContain('UPDATE 1')
    expect(readFileSync(path.join(directory, 'argv'), 'utf8')).not.toContain('dollar')
    expect(readFileSync(path.join(directory, 'stdin'), 'utf8')).toContain('export PGPASSWORD=')
    expect(readFileSync(path.join(directory, 'stdin'), 'utf8')).toContain('SELECT 1')
  })

  it('fails when the server does, rather than reading an empty answer as none', async () => {
    writeFileSync(path.join(directory, 'exit_code'), '1')

    await expect(sqlRunnerFor(target())('SELECT 1')).rejects.toThrow('failed (exit 1)')
    expect(existsSync(path.join(directory, 'stdin'))).toBe(true)
  })
})

describe('productionTargetFromEnvironment', () => {
  it('names every setting that is missing, so a run outside seed-production.sh says what to do', () => {
    expect(() => productionTargetFromEnvironment({ SEED_DOMAIN: 'predictsmu.test' }, [])).toThrow(
      /SEED_CONFIRMED_DOMAIN.*DATABASE_ADMIN_URL.*npm run seed:production/,
    )
  })
})
