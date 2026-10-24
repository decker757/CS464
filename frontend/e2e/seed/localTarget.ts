// #454: "Nothing in it can run against a non-local database by accident." The
// seed reaches the database two ways: through the three services' URLs, and
// through `docker compose exec db` for the two SQL steps the e2e helpers take
// (promoting the first admin, backdating a dispute window). Both are checked,
// before the first request, and anything that cannot be read is refused.
//
// There is no override. A database on another machine is seeded by running
// this on that machine, against its own localhost.

const LOCAL_HOSTS = ['localhost', '127.0.0.1', '[::1]']

// A Docker engine on this machine: a Unix socket, or a named pipe on this
// machine's own `//./` (Docker Desktop on Windows). Anything else (tcp://,
// ssh://, a pipe on another host) may be another machine.
const LOCAL_DOCKER_PREFIXES = ['unix://', 'npipe:////./']

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
  return LOCAL_HOSTS.includes(hostname)
}

function isLocalDockerEndpoint(endpoint: string): boolean {
  return LOCAL_DOCKER_PREFIXES.some(prefix => endpoint.startsWith(prefix))
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
