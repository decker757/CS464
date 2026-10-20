import { Link } from 'react-router-dom'
import { useHomePath } from '../../hooks/useHomePath'
import Logo from '../ui/Logo'

/** The navy bar across the top of every page: the logo, then whatever controls the page passes. */
export default function TopBar({ children }: { children?: React.ReactNode }) {
  // The wordmark is the one way back that every page has, so it points at the
  // page that visitor calls home.
  const home = useHomePath()

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
