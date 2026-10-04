import { Spinner } from './ui/icons'

/** Shown by ProtectedRoute while the session is still unknown.
 *
 * `role="status"` and the label are what a screen reader announces, since the
 * spinner is `aria-hidden`. They are also what the test finds this screen by.
 */
export default function LoadingScreen() {
  return (
    <div role="status" aria-label="Loading" className="flex min-h-screen items-center justify-center bg-smu-cream text-smu-navy">
      <Spinner />
    </div>
  )
}
