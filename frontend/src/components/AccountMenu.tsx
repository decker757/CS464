import { useEffect, useRef, useState } from 'react'
import { useFocusTrap } from '../hooks/useFocusTrap'
import Button from './ui/Button'
import { ChevronDownIcon } from './ui/icons'

interface AccountMenuProps {
  username: string
  email: string
  /** True while the sign-out request is in flight. */
  isLoggingOut: boolean
  onLogout: () => void
}

const PANEL_ID = 'account-menu-panel'

interface AccountMenuPanelProps {
  email: string
  isLoggingOut: boolean
  onLogout: () => void
  onClose: () => void
}

/** The open menu. Its own component because it is mounted only while the menu
 * is open, and `useFocusTrap` moving focus in and handing it back are that
 * mount and unmount.
 *
 * `role="dialog"` rather than `role="menu"`: a menu promises arrow-key
 * navigation between its items, and this holds who you are signed in as and
 * one control. What it does promise is the trap's four — focus in, Tab kept
 * inside, Escape out, focus back on the button — plus the click elsewhere
 * below. */
function AccountMenuPanel({ email, isLoggingOut, onLogout, onClose }: AccountMenuPanelProps) {
  const panelRef = useRef<HTMLDivElement>(null)
  useFocusTrap(panelRef, { onClose })

  return (
    <div
      ref={panelRef}
      id={PANEL_ID}
      role="dialog"
      aria-label="Account"
      tabIndex={-1}
      className="absolute top-full right-0 z-10 mt-2 w-60 rounded-xl border border-line bg-white p-3 shadow-float"
    >
      <p className="mb-3 px-1 text-xs break-words text-subtle">Signed in as {email}</p>
      <Button variant="outline" size="xs" className="w-full" onClick={onLogout} disabled={isLoggingOut}>
        {isLoggingOut ? 'Signing out…' : 'Log Out'}
      </Button>
    </div>
  )
}

/**
 * The account cluster's one control: a button carrying the signed-in
 * username, and the menu it opens holding the email and Log Out.
 *
 * It is a button, and looks like one, because the balance and the username
 * used to carry the navigation's own classes — the balance the active link's
 * exactly — so the row read as seven places to go, two of which did nothing
 * when clicked (#437).
 */
export default function AccountMenu({ username, email, isLoggingOut, onLogout }: AccountMenuProps) {
  const [isOpen, setIsOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!isOpen) return
    // The ref wraps the button as well as the panel, so a click on the button
    // is not "elsewhere": it closes through the button's own toggle instead of
    // being closed here and reopened by the click that follows.
    const closeOnClickElsewhere = (event: MouseEvent) => {
      const menu = menuRef.current
      if (menu && event.target instanceof Node && !menu.contains(event.target)) setIsOpen(false)
    }
    document.addEventListener('mousedown', closeOnClickElsewhere)
    return () => document.removeEventListener('mousedown', closeOnClickElsewhere)
  }, [isOpen])

  return (
    <div ref={menuRef} className="relative">
      <button
        type="button"
        onClick={() => setIsOpen(open => !open)}
        aria-expanded={isOpen}
        aria-controls={PANEL_ID}
        className="flex cursor-pointer items-center gap-2 rounded-full py-1 pr-3 pl-1 text-sm text-white transition hover:bg-white/15"
      >
        {/* Decorative: the username is right beside it, and a screen reader
            reading "A alice" would be reading the same name twice. */}
        <span aria-hidden="true" className="flex h-7 w-7 items-center justify-center rounded-full bg-smu-gold text-xs font-bold text-smu-navy">
          {username.slice(0, 1).toUpperCase()}
        </span>
        <span>{username}</span>
        <ChevronDownIcon className={isOpen ? 'rotate-180' : undefined} />
      </button>
      {isOpen && (
        <AccountMenuPanel email={email} isLoggingOut={isLoggingOut} onLogout={onLogout} onClose={() => setIsOpen(false)} />
      )}
    </div>
  )
}
