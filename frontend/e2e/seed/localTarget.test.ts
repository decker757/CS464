import { describe, expect, it } from 'vitest'
import { refuseDevComposeProject, refuseUnlessLocal } from './localTarget'

// #454: "Nothing in it can run against a non-local database by accident." The
// seed writes through the three service URLs and runs its two SQL steps
// through the Docker engine, so both are checked before the first request.

const LOCAL_URLS = ['http://localhost:8000', 'http://127.0.0.1:8001', 'http://localhost:8003']
const DOCKER_DESKTOP = 'npipe:////./pipe/dockerDesktopLinuxEngine'

describe('refuseUnlessLocal', () => {
  it('accepts the stack on this machine, on Windows and on Linux or macOS', () => {
    expect(() => refuseUnlessLocal({ serviceUrls: LOCAL_URLS, dockerEndpoint: DOCKER_DESKTOP })).not.toThrow()
    expect(() => refuseUnlessLocal({ serviceUrls: LOCAL_URLS, dockerEndpoint: 'unix:///var/run/docker.sock' })).not.toThrow()
  })

  it('refuses a service URL on another host, and names it', () => {
    const oneRemote = ['http://localhost:8000', 'https://market.gembet.example', 'http://localhost:8003']

    expect(() => refuseUnlessLocal({ serviceUrls: oneRemote, dockerEndpoint: DOCKER_DESKTOP })).toThrow(
      'https://market.gembet.example',
    )
  })

  it('refuses a host that only begins with localhost', () => {
    const lookalike = ['http://localhost.gembet.example:8000', 'http://localhost:8001', 'http://localhost:8003']

    expect(() => refuseUnlessLocal({ serviceUrls: lookalike, dockerEndpoint: DOCKER_DESKTOP })).toThrow(
      'http://localhost.gembet.example:8000',
    )
  })

  it('refuses a service URL it cannot read, rather than letting it through', () => {
    // A typo in the port: `new URL` throws on it, so there is no host to check.
    const unreadable = ['http://localhost:80o0', 'http://localhost:8001', 'http://localhost:8003']

    expect(() => refuseUnlessLocal({ serviceUrls: unreadable, dockerEndpoint: DOCKER_DESKTOP })).toThrow('http://localhost:80o0')
  })

  // tcp:// is refused even to localhost: failing closed costs one unusual setup.
  // A named pipe counts only on this machine's own `//./` prefix.
  it.each(['ssh://deploy@gembet.example', 'tcp://localhost:2375', 'npipe://buildbox/pipe/docker_engine'])(
    'refuses the Docker engine %s, where the SQL steps would run',
    endpoint => {
      expect(() => refuseUnlessLocal({ serviceUrls: LOCAL_URLS, dockerEndpoint: endpoint })).toThrow(endpoint)
    },
  )

  it('names every address it refuses in one error, not only the first', () => {
    const twoRemote = ['https://auth.gembet.example', 'http://localhost:8001', 'https://ledger.gembet.example']
    const target = { serviceUrls: twoRemote, dockerEndpoint: 'ssh://deploy@gembet.example' }

    expect(() => refuseUnlessLocal(target)).toThrow('https://auth.gembet.example')
    expect(() => refuseUnlessLocal(target)).toThrow('https://ledger.gembet.example')
    expect(() => refuseUnlessLocal(target)).toThrow('ssh://deploy@gembet.example')
  })
})

// #454's review: a stack on this machine is not yet the demo's. Without
// COMPOSE_PROJECT_NAME, compose picks docker-compose.yml's `cs464`, the dev
// stack, whose ledger entries the seed could never take back out.
describe('refuseDevComposeProject', () => {
  it('refuses the everyday dev stack, and says how to name another project', () => {
    expect(() => refuseDevComposeProject('cs464')).toThrow('COMPOSE_PROJECT_NAME=cs464-demo')
  })

  it("accepts the demo's own project", () => {
    expect(() => refuseDevComposeProject('cs464-demo')).not.toThrow()
  })
})
