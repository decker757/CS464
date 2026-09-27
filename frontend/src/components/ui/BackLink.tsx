import { Link } from 'react-router-dom'

/** "← Markets"-style link back to a parent page. Pass `block` or `inline-block` in `className`. */
export default function BackLink({ to, label, className = '' }: { to: string; label: string; className?: string }) {
  return (
    <Link to={to} className={`text-[13px] text-muted ${className}`}>
      ← {label}
    </Link>
  )
}
