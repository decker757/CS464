/**
 * A stand-in for the browser WebSocket that a test drives by hand.
 *
 * Shared because two hooks open sockets — the detail page's single market and
 * the browse list's rendered set ([X-1] #444) — and a second copy of this
 * would be a second set of answers to "when does onclose fire".
 *
 * Install it per test rather than once for the run: MSW installs its own
 * WebSocket when the server starts.
 *
 * ```ts
 * beforeEach(() => {
 *   FakeSocket.instances = []
 *   vi.stubGlobal('WebSocket', FakeSocket)
 * })
 * afterEach(() => vi.unstubAllGlobals())
 * ```
 */
export class FakeSocket {
  static OPEN = 1
  static instances: FakeSocket[] = []
  readyState = 0
  sent: { action: string; market_id: string }[] = []
  onopen: (() => void) | null = null
  onmessage: ((e: { data: string }) => void) | null = null
  onclose: ((e: { code: number }) => void) | null = null
  constructor() { FakeSocket.instances.push(this) }
  send(data: string) { this.sent.push(JSON.parse(data)) }
  // A browser fires `close` asynchronously, for our own close() as well as the server's.
  close() {
    if (this.readyState === 3) return
    this.readyState = 3
    setTimeout(() => this.onclose?.({ code: 1005 }), 0)
  }
  open() { this.readyState = 1; this.onopen?.() }
  receive(msg: object) { this.onmessage?.({ data: JSON.stringify(msg) }) }
  serverClose(code: number) { this.readyState = 3; this.onclose?.({ code }) }
}

/** The socket most recently constructed: "the" socket, in a test that opens one. */
export const latestSocket = () => FakeSocket.instances[FakeSocket.instances.length - 1]
