import { describe, expect, it } from 'vitest'
import { refuseUnlessLocal } from './localTarget'

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

  it('accepts the compose service names, for a run inside the compose network', () => {
    const insideCompose = ['http://auth:8000', 'http://market:8000', 'http://ledger:8000']

    expect(() => refuseUnlessLocal({ serviceUrls: insideCompose, dockerEndpoint: DOCKER_DESKTOP })).not.toThrow()
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
    const unreadable = ['localhost:8000', 'http://localhost:8001', 'http://localhost:8003']

    expect(() => refuseUnlessLocal({ serviceUrls: unreadable, dockerEndpoint: DOCKER_DESKTOP })).toThrow('localhost:8000')
  })

  it('refuses a Docker engine on another machine, where the SQL steps would run', () => {
    expect(() => refuseUnlessLocal({ serviceUrls: LOCAL_URLS, dockerEndpoint: 'ssh://deploy@gembet.example' })).toThrow(
      'ssh://deploy@gembet.example',
    )
  })
})
