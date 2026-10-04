import { Link } from 'react-router-dom'
import TopBar from '../layout/TopBar'
import { buttonClass } from '../ui/buttonClass'

export default function LandingNavbar() {
  return (
    <TopBar>
      <Link to="/login" className={buttonClass('outlineOnNavy', 'sm')}>Log In</Link>
      <Link to="/register" className={buttonClass('gold', 'sm')}>Register</Link>
    </TopBar>
  )
}
