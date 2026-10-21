import { Link } from 'react-router-dom'
import { useHomePath } from '../../hooks/useHomePath'
import Logo from '../ui/Logo'

interface TopBarProps {
  /** The page's navigation links. They get the `<nav>` landmark to
   * themselves, so nothing that is not somewhere to go is announced as part
   * of the navigation (#437). */
  nav?: React.ReactNode
  /** The page's own controls, at the far right: the signed-in account
   * cluster, or the landing page's Log In and Register. */
  children?: React.ReactNode
}

/** The navy bar across the top of every page: the logo, the page's
 * navigation, then whatever controls the page passes. */
export default function TopBar({ nav, children }: TopBarProps) {
  // The wordmark is the one way back that every page has, so it points at the
  // page that visitor calls home.
  const home = useHomePath()

  return (
    <header className="bg-smu-navy">
      <div className="mx-auto flex h-[72px] max-w-[1200px] items-center gap-8 px-8">
        <Link
          to={home}
          aria-label="PredictSMU home"
          className="rounded-lg focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-smu-gold"
        >
          <Logo className="text-xl text-white" />
        </Link>
        {/* The navigation takes the space that is left, which is what puts the
            controls at the far right however many links there are. A page with
            no navigation of its own gets the spacer and no empty landmark. */}
        {nav ? <nav aria-label="Main" className="flex flex-1 items-center gap-4">{nav}</nav> : <div className="flex-1" />}
        <div className="flex items-center gap-3">{children}</div>
      </div>
    </header>
  )
}
