/**
 * A promise for a mock handler to await, and the function that settles it: the
 * handler's answer waits until the test calls `release`, so the test can act
 * while a request is still on its way.
 */
export function holdUntilReleased(): { held: Promise<void>; release: () => void } {
  let release!: () => void
  const held = new Promise<void>(resolve => { release = resolve })
  return { held, release }
}
