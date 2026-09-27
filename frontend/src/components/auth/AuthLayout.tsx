import Eyebrow from '../ui/Eyebrow'
import { ChevronLeftIcon } from '../ui/icons'
import Logo from '../ui/Logo'
import BrandPanel from './BrandPanel'

interface AuthLayoutProps {
  eyebrow: string
  title: string
  subtitle: string
  /** A form-level error from the server, shown above the form. */
  error?: string
  /** The "already have an account?" line under the form. */
  footer: React.ReactNode
  children: React.ReactNode
}

/** The shared frame of the login and register pages. */
export default function AuthLayout({ eyebrow, title, subtitle, error, footer, children }: AuthLayoutProps) {
  return (
    <div className="flex min-h-screen">
      <BrandPanel />

      <div className="flex flex-1 flex-col items-center justify-center bg-smu-cream px-[clamp(20px,5vw,48px)] py-[clamp(24px,4vw,48px)]">
        <a href="/" className="mb-7 md:hidden">
          <Logo iconSize={18} className="text-lg text-smu-navy" />
        </a>

        <div className="w-full max-w-[460px] rounded-[20px] border border-smu-gold/20 bg-white p-[clamp(24px,4vw,36px)] shadow-panel">
          <a href="/" className="mb-5 inline-flex items-center gap-1 text-[13px] text-subtle transition-colors hover:text-smu-navy">
            <ChevronLeftIcon />
            Back to home
          </a>

          <Eyebrow className="mb-1.5 text-[11px]">{eyebrow}</Eyebrow>
          <h1 className="mb-1 text-2xl font-extrabold tracking-[-0.3px] text-smu-navy">{title}</h1>
          <p className="mb-5 text-sm leading-normal text-muted">{subtitle}</p>

          {error && (
            <div role="alert" className="mb-4 rounded-control border border-danger/25 bg-danger/5 px-3.5 py-[11px] text-[13px] text-danger">
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

