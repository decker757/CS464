import { CREAM, NAV } from '../theme/colors'
import { Spinner } from './auth/AuthIcons'

/** Shown by ProtectedRoute while the session is still unknown.
 *
 * `role="status"` and the label are not decoration: `Spinner` is `aria-hidden`,
 * so without them this screen is an empty document to a screen reader — exactly
 * the blank page it exists to replace. They are also the only thing a test can
 * name, and asserting "the container is not empty" instead passed with this
 * whole component deleted.
 */
export default function LoadingScreen() {
  return (
    <div
      role="status"
      aria-label="Loading"
      style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', backgroundColor: CREAM, color: NAV }}
    >
      <Spinner />
    </div>
  )
}
