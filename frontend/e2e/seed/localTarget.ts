// #454: "Nothing in it can run against a non-local database by accident." The
// seed reaches the database two ways: through the three services' URLs, and
// through `docker compose exec db` for the two SQL steps the e2e helpers take
// (promoting the first admin, backdating a dispute window). Both are checked,
// before the first request, and anything that cannot be read is refused.
//
// There is no override. A database on another machine is seeded by running
// this on that machine, against its own localhost.

const LOCAL_HOSTS = ['localhost', '127.0.0.1', '[::1]']

// The compose service names the seed calls, for a run from a container on the
// compose network. They resolve only there.
const COMPOSE_SERVICES = ['auth', 'market', 'ledger']

// A Docker engine on this machine: a Unix socket, or Docker Desktop's named
// pipe on Windows. Anything else (tcp://, ssh://) may be another machine.
const LOCAL_DOCKER_SCHEMES = ['unix:', 'npipe:']

export interface SeedTarget {
  serviceUrls: string[]
  dockerEndpoint: string
}

function isLocalServiceUrl(url: string): boolean {
  let hostname: string
  try {
    hostname = new URL(url).hostname
  } catch {
    return false
  }
  return LOCAL_HOSTS.includes(hostname) || COMPOSE_SERVICES.includes(hostname)
}

function isLocalDockerEndpoint(endpoint: string): boolean {
  const scheme = endpoint.slice(0, endpoint.indexOf(':') + 1)
  return LOCAL_DOCKER_SCHEMES.includes(scheme)
}

/** Throws, naming every address that is not on this machine, unless all of them are. */
export function refuseUnlessLocal(target: SeedTarget): void {
  const refused: string[] = []
  for (const url of target.serviceUrls) {
    if (!isLocalServiceUrl(url)) refused.push(`service URL ${url}`)
  }
  if (!isLocalDockerEndpoint(target.dockerEndpoint)) refused.push(`Docker engine ${target.dockerEndpoint}`)

  if (refused.length > 0) {
    throw new Error(
      `Refusing to seed: the demo seed only runs against a stack on this machine, and these are not local: ` +
        `${refused.join(', ')}. Point VITE_API_URL, VITE_MARKET_API_URL, VITE_LEDGER_API_URL and DOCKER_HOST ` +
        `at this machine, or unset them.`,
    )
  }
}
