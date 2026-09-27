import TopBar from '../layout/TopBar'
import { buttonClass } from '../ui/buttonClass'

export default function LandingNavbar() {
  return (
    <TopBar>
      <a href="/login" className={buttonClass('outlineOnNavy', 'sm')}>Log In</a>
      <a href="/register" className={buttonClass('gold', 'sm')}>Register</a>
    </TopBar>
  )
}
