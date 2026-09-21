import { CREAM, NAV } from '../theme/colors'
import { Spinner } from './auth/AuthIcons'

export default function LoadingScreen() {
  return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', backgroundColor: CREAM, color: NAV }}>
      <Spinner />
    </div>
  )
}
