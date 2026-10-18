import { Link } from 'react-router-dom'
import { useAuth } from '../../context/AuthContext'
import Logo from '../ui/Logo'

/** The navy bar across the top of every page: the logo, then whatever controls the page passes. */
export default function TopBar({ children }: { children?: React.ReactNode }) {
  const { user } = useAuth()

  // The wordmark is the one way back that every page has, so it points at the
  // page that visitor calls home: the market list once signed in, the landing
  // page otherwise. `undefined` is "not known yet", and the pages rendered in
  // that state are the guest ones (GuestOnly renders while it resolves).
  const home = user ? '/markets' : '/'

  return (
    <header className="bg-smu-navy">
      <nav className="mx-auto flex h-[72px] max-w-[1200px] items-center justify-between px-8">
        <Link
          to={home}
          aria-label="PredictSMU home"
          className="rounded-lg focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-smu-gold"
        >
          <Logo className="text-xl text-white" />
        </Link>
        <div className="flex items-center gap-3">{children}</div>
      </nav>
    </header>
  )
}
