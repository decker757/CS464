import { Link } from 'react-router-dom'
import { useDocumentTitle } from '../../hooks/useDocumentTitle'
import Eyebrow from '../ui/Eyebrow'
import { ChevronLeftIcon } from '../ui/icons'
import Logo from '../ui/Logo'
import BrandPanel from './BrandPanel'

interface AuthLayoutProps {
  eyebrow: string
  title: string
  subtitle: string
  /** Something the visitor should know that is nobody's mistake, like a
   *  session that ended on its own. Sits above `error`, which answers what
   *  they just did and so belongs nearer the form. */
  notice?: string
  /** A form-level error from the server, shown above the form. */
  error?: string
  /** The "already have an account?" line under the form. */
  footer: React.ReactNode
  children: React.ReactNode
}

const MESSAGE_CLASS = 'mb-4 rounded-control border px-3.5 py-[11px] text-[13px]'

/** The shared frame of the login and register pages.
 *
 * Names the tab after `title` for the same reason `PageTitle` does (#356):
 * the heading and the tab are then one string, and the landing page is left
 * with index.html's own title, which already names the product better than
 * "Home" would. */
export default function AuthLayout({ eyebrow, title, subtitle, notice, error, footer, children }: AuthLayoutProps) {
  useDocumentTitle(title)

  return (
    <div className="flex min-h-screen">
      <BrandPanel />

      <div className="flex flex-1 flex-col items-center justify-center bg-smu-cream px-[clamp(20px,5vw,48px)] py-[clamp(24px,4vw,48px)]">
        <Link to="/" className="mb-7 md:hidden">
          <Logo iconSize={18} className="text-lg text-smu-navy" />
        </Link>

        <div className="w-full max-w-[460px] rounded-[20px] border border-smu-gold/20 bg-white p-[clamp(24px,4vw,36px)] shadow-panel">
          <Link to="/" className="mb-5 inline-flex items-center gap-1 text-[13px] text-subtle transition-colors hover:text-smu-navy">
            <ChevronLeftIcon />
            Back to home
          </Link>

          <Eyebrow className="mb-1.5 text-[11px]">{eyebrow}</Eyebrow>
          <h1 className="mb-1 text-2xl font-extrabold tracking-[-0.3px] text-smu-navy">{title}</h1>
          <p className="mb-5 text-sm leading-normal text-muted">{subtitle}</p>

          {/* `status`, not `alert`: nobody did anything wrong, and the form's
              own messages are the alerts on these pages. */}
          {notice && (
            <p role="status" className={`${MESSAGE_CLASS} border-info/20 bg-info/5 text-info-strong`}>
              {notice}
            </p>
          )}

          {error && (
            <div role="alert" className={`${MESSAGE_CLASS} border-danger/25 bg-danger/5 text-danger`}>
              {error}
            </div>
          )}

          {children}

          <p className="mt-5 text-center text-sm text-muted">{footer}</p>
        </div>
      </div>
    </div>
  )
}

