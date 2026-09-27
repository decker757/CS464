import { Link } from 'react-router-dom'

/** The navy, underlined link in an auth page's footer line. */
export default function AuthFooterLink({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <Link to={to} className="border-b border-smu-navy/25 font-semibold text-smu-navy transition-colors hover:border-smu-navy">
      {children}
    </Link>
  )
}
