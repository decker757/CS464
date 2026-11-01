// #609. What seeding the production site needs that seeding a local stack does
// not. The seed itself is the same (seedDemo.seed.ts); seed-production.sh
// collects Terraform's outputs and the person's confirmation, and passes them
// here through the environment.
//
// Two things differ from a local seed, and both are why the local seed refuses
// production (#454):
// - Every account gets its own generated password, written to a file outside
//   the repository. The local seed's one password is in the repository, so on
//   a public site anyone could log in as one of the seeded admins.
// - The two SQL steps run on an app server over SSH, because production's
//   database is reachable only from there (ADR 0026).
import { spawn } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { appendFileSync, closeSync, openSync } from 'node:fs'
import path from 'node:path'
import type { SqlRunner } from '../support/backend'

export interface ProductionTarget {
  domain: string
  confirmedDomain: string
  serviceUrls: string[]
  sqlHost: string
  sshKeyFile: string
  knownHostsFile: string
  databaseAdminUrl: string
  accountsFile: string
}

// The image ensure-database.sh uses, for the same psql.
const PSQL_IMAGE = 'postgres:17.2-alpine'

/** Throws unless the person running the seed typed the domain it will seed. */
export function refuseUnlessConfirmed(domain: string, confirmedDomain: string): void {
  if (confirmedDomain === domain) return
  throw new Error(
    `Refusing to seed: "${confirmedDomain}" was typed to confirm, and the site is ${domain}. ` +
      'Seeding is permanent, because ledger entries are never deleted.',
  )
}

/** Throws unless every service URL is https and on the domain being seeded. */
export function refuseUnlessOnDomain(serviceUrls: string[], domain: string): void {
  const elsewhere = serviceUrls.filter(url => {
    try {
      const parsed = new URL(url)
      return parsed.protocol !== 'https:' || !parsed.hostname.endsWith(`.${domain}`)
    } catch {
      return true
    }
  })
  if (elsewhere.length === 0) return
  throw new Error(`Refusing to seed ${domain}: these service URLs are not https on it: ${elsewhere.join(', ')}`)
}

/** Throws unless production has no market yet. The seed is one-shot, and permanent. */
export function refuseIfMarketsExist(marketCount: number): void {
  if (marketCount === 0) return
  throw new Error(
    `Refusing to seed: production already has ${marketCount} market(s). The seed only goes into an empty site.`,
  )
}

/**
 * Throws if any account the seed makes already exists, or any administrator
 * does. The first means an earlier run that stopped partway; a rerun would
 * fail at that name, after writing it a fresh, wrong password. The second
 * means the site already has its first administrator, and the seed makes its
 * own by hand, which only the first may be (ADR 0007).
 */
export function refuseIfAccountsExist(seededNamesTaken: number, administrators: number): void {
  if (seededNamesTaken > 0) {
    throw new Error(
      `Refusing to seed: ${seededNamesTaken} of the seed's account names already exist, so an earlier run ` +
        'stopped partway. docs/deployment.md, "If the seed stops partway".',
    )
  }
  if (administrators > 0) {
    throw new Error(
      'Refusing to seed: the site already has an administrator. Seed first: the seed makes the first ' +
        'administrator, who then promotes everyone else from the site (ADR 0007).',
    )
  }
}

/** The SQL that counts what the seed must not find. */
export function preflightSql(seededUsernames: string[]): string {
  for (const username of seededUsernames) {
    if (!/^\w+$/.test(username)) throw new Error(`refusing to put ${username} in SQL`)
  }
  const names = seededUsernames.map(username => `'${username.toLowerCase()}'`).join(', ')
  return [
    `SELECT 'markets=' || count(*) FROM market.markets;`,
    `SELECT 'seeded_names=' || count(*) FROM auth.users WHERE lower(username) IN (${names});`,
    `SELECT 'administrators=' || count(*) FROM auth.users WHERE role = 'admin';`,
  ].join('\n')
}

/** One count from psql's output for preflightSql, by its label. */
export function countOf(label: string, psqlOutput: string): number {
  const match = new RegExp(`${label}=(\\d+)`).exec(psqlOutput)
  if (!match) throw new Error(`could not read ${label} from: ${psqlOutput}`)
  return Number(match[1])
}

/** Throws unless the passwords file is outside the repository, where nothing could commit it. */
export function refuseInsideRepository(file: string, repositoryRoot: string): void {
  const fromRoot = path.relative(path.resolve(repositoryRoot), path.resolve(file))
  const isInside = fromRoot === '' || (!fromRoot.startsWith('..') && !path.isAbsolute(fromRoot))
  if (!isInside) return
  throw new Error(`Refusing to write the passwords to ${path.resolve(file)}: it is inside the repository.`)
}

/**
 * Each seeded account's password: generated the first time it is asked for,
 * and the same after. Every one is appended to a file readable by its owner
 * only, as it is made, so a run that stops partway still records them.
 */
export class SeededAccounts {
  private readonly passwords = new Map<string, string>()

  /** Creates the file, and refuses one that already exists: it would mean an earlier run. */
  constructor(private readonly file: string) {
    closeSync(openSync(file, 'wx', 0o600))
    appendFileSync(file, '# Production demo accounts (#609). Keep this file private.\n')
  }

  passwordFor(username: string): string {
    const known = this.passwords.get(username)
    if (known) return known
    const password = randomBytes(24).toString('base64url')
    this.passwords.set(username, password)
    appendFileSync(this.file, `${username} ${password}\n`)
    return password
  }
}

/** A string bash reads back exactly, whatever it contains. */
function shellQuoted(text: string): string {
  return `'${text.replaceAll("'", `'\\''`)}'`
}

/** ssh's arguments. The admin URL is not among them: `ps` would show it. */
export function sshArgumentsFor(target: ProductionTarget): string[] {
  return [
    '-i', target.sshKeyFile,
    '-o', 'IdentitiesOnly=yes',
    '-o', `UserKnownHostsFile=${target.knownHostsFile}`,
    '-o', 'StrictHostKeyChecking=yes',
    '-o', 'BatchMode=yes',
    '-o', 'ConnectTimeout=15',
    `deploy@${target.sqlHost}`,
    'bash', '-s',
  ]
}

/**
 * The admin URL as libpq's own variables. psql reads these from its
 * environment, so the password is never an argument, where `ps` on the
 * server would show it. libpq does not read a whole URL from one variable.
 */
export function connectionVariablesOf(databaseAdminUrl: string): Record<string, string> {
  const url = new URL(databaseAdminUrl)
  return {
    PGHOST: url.hostname,
    PGPORT: url.port || '5432',
    PGUSER: decodeURIComponent(url.username),
    PGPASSWORD: decodeURIComponent(url.password),
    PGDATABASE: decodeURIComponent(url.pathname.slice(1)),
    PGSSLMODE: url.searchParams.get('sslmode') ?? 'require',
  }
}

/** The script the app server runs: psql as the database's admin, with the SQL on stdin. */
export function remotePsqlScript(databaseAdminUrl: string, sql: string): string {
  const variables = connectionVariablesOf(databaseAdminUrl)
  const exports = Object.entries(variables).map(([name, value]) => `export ${name}=${shellQuoted(value)}`)
  const passThrough = Object.keys(variables).map(name => `-e ${name}`).join(' ')
  return [
    ...exports,
    `docker run --rm -i ${passThrough} ${PSQL_IMAGE} psql -v ON_ERROR_STOP=1 <<'SQL_END'`,
    sql,
    'SQL_END',
    '',
  ].join('\n')
}

/** Runs SQL as production's database admin, on an app server, and returns psql's output. */
export function sqlRunnerFor(target: ProductionTarget): SqlRunner {
  return sql =>
    new Promise((resolve, reject) => {
      const ssh = spawn('ssh', sshArgumentsFor(target), { stdio: ['pipe', 'pipe', 'pipe'] })
      let stdout = ''
      let stderr = ''
      ssh.stdout.on('data', chunk => (stdout += chunk))
      ssh.stderr.on('data', chunk => (stderr += chunk))
      ssh.on('error', reject)
      ssh.on('close', code => {
        if (code === 0) resolve(stdout)
        else reject(new Error(`SQL on ${target.sqlHost} failed (exit ${code}): ${stderr}`))
      })
      ssh.stdin.end(remotePsqlScript(target.databaseAdminUrl, sql))
    })
}

/** The production target, from the variables seed-production.sh sets. Throws naming any missing. */
export function productionTargetFromEnvironment(environment: NodeJS.ProcessEnv, serviceUrls: string[]): ProductionTarget {
  const names = [
    'SEED_DOMAIN', 'SEED_CONFIRMED_DOMAIN', 'SEED_SQL_HOST', 'SEED_SSH_KEY',
    'SEED_KNOWN_HOSTS', 'DATABASE_ADMIN_URL', 'SEED_ACCOUNTS_FILE',
  ]
  const missing = names.filter(name => !environment[name])
  if (missing.length > 0) {
    throw new Error(`Seeding production needs ${missing.join(', ')}. Run it through npm run seed:production.`)
  }
  return {
    domain: environment.SEED_DOMAIN as string,
    confirmedDomain: environment.SEED_CONFIRMED_DOMAIN as string,
    serviceUrls,
    sqlHost: environment.SEED_SQL_HOST as string,
    sshKeyFile: environment.SEED_SSH_KEY as string,
    knownHostsFile: environment.SEED_KNOWN_HOSTS as string,
    databaseAdminUrl: environment.DATABASE_ADMIN_URL as string,
    accountsFile: environment.SEED_ACCOUNTS_FILE as string,
  }
}
