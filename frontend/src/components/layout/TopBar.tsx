import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useCloseOnClickElsewhere } from '../../hooks/useCloseOnClickElsewhere'
import { useHomePath } from '../../hooks/useHomePath'
import Logo from '../ui/Logo'
import { MenuIcon } from '../ui/icons'

interface TopBarProps {
  /** The page's navigation links. They get the `<nav>` landmark to
   * themselves, so nothing that is not somewhere to go is announced as part
   * of the navigation (#437). */
  nav?: React.ReactNode
  /** The page's own controls, at the far right: the signed-in account
   * cluster, or the landing page's Log In and Register. */
  children?: React.ReactNode
}

const NAV_LINKS_ID = 'main-navigation-links'

/**
 * The navigation, in the bar from `xl` up and behind a Menu button below it
 * (#438). An administrator's row of seven links and a button needs most of a
 * laptop's width on its own, so anything narrower either hides it or scrolls
 * the page sideways, which is what every signed-in screen did.
 *
 * A disclosure and not a dialog: it covers nothing the keyboard has to be
 * kept out of, so focus stays on the button when it opens and the links are
 * simply the next stops on Tab. It closes on Escape (focus back on the
 * button), on a chosen link, when focus moves out of it and on a click
 * elsewhere — every way of being done with it.
 *
 * The links are rendered once, for both sizes: below `xl` the list is hidden
 * until opened and drops down under the bar, and from `xl` the same list is
 * always shown in the row.
 */
function MainNavigation({ children }: { children: React.ReactNode }) {
  const [isOpen, setIsOpen] = useState(false)
  const navRef = useRef<HTMLElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!isOpen) return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      setIsOpen(false)
      // Focus may be on a link that is about to be hidden.
      buttonRef.current?.focus()
    }
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [isOpen])

  // The nav holds the button as well as the links (see the hook).
  useCloseOnClickElsewhere(navRef, isOpen, () => setIsOpen(false))

  // Only when focus lands somewhere else. A blur to nowhere is a click on
  // the page, which the listener above already judges by where it landed;
  // closing on it here would hide a link between its mousedown and its click
  // in a browser that does not focus links on click.
  const handleBlur = (event: React.FocusEvent<HTMLElement>) => {
    const next = event.relatedTarget
    if (next instanceof Node && !event.currentTarget.contains(next)) setIsOpen(false)
  }

  // Any link, including the one to the page already open, which changes no
  // route and so would leave the menu open if this waited for the address.
  const handleLinkClick = (event: React.MouseEvent<HTMLElement>) => {
    if (event.target instanceof Element && event.target.closest('a')) setIsOpen(false)
  }

  // Below `xl` the list shows only while open; `xl:flex` shows it from there
  // on whatever this says.
  const linksDisplayBelowXl = isOpen ? 'flex' : 'hidden'

  return (
    <nav ref={navRef} aria-label="Main" className="flex flex-1 items-center" onBlur={handleBlur}>
      <button
        ref={buttonRef}
        type="button"
        aria-expanded={isOpen}
        aria-controls={NAV_LINKS_ID}
        onClick={() => setIsOpen(open => !open)}
        className="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-sm text-white transition hover:bg-white/15 focus-visible:outline-2 focus-visible:outline-smu-gold xl:hidden"
      >
        <MenuIcon />
        <span className="sr-only sm:not-sr-only">Menu</span>
      </button>
      <div
        id={NAV_LINKS_ID}
        onClick={handleLinkClick}
        className={`${linksDisplayBelowXl} absolute inset-x-0 top-full z-20 flex-col items-start gap-4 border-t border-white/10 bg-smu-navy px-4 py-5 shadow-float sm:px-8 xl:static xl:flex xl:flex-1 xl:flex-row xl:items-center xl:border-0 xl:p-0 xl:shadow-none`}
      >
        {children}
      </div>
    </nav>
  )
}

/** The navy bar across the top of every page: the logo, the page's
 * navigation, then whatever controls the page passes. */
export default function TopBar({ nav, children }: TopBarProps) {
  // The wordmark is the one way back that every page has, so it points at the
  // page that visitor calls home.
  const home = useHomePath()

  return (
    <header className="bg-smu-navy">
      {/* `relative` is what the dropped-down navigation is placed against,
          so it spans the bar's full width under it. */}
      <div className="relative mx-auto flex h-[72px] max-w-[1200px] items-center gap-3 px-4 sm:gap-8 sm:px-8">
        <Link
          to={home}
          aria-label="PredictSMU home"
          className="rounded-lg focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-smu-gold"
        >
          {/* On a phone the signed-in bar needs the room for the Menu button
              and the balance, so its wordmark is the icon alone there; the
              link's label still names it. The landing page's bar is left as
              it was. */}
          <Logo className="text-xl text-white" wordmarkClassName={nav ? 'sr-only sm:not-sr-only' : undefined} />
        </Link>
        {/* The navigation takes the space that is left, which is what puts the
            controls at the far right however many links there are. A page with
            no navigation of its own gets the spacer and no empty landmark. */}
        {nav ? <MainNavigation>{nav}</MainNavigation> : <div className="flex-1" />}
        <div className="flex items-center gap-3">{children}</div>
      </div>
    </header>
  )
}
