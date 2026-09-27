/** The navy, underlined link in an auth page's footer line. */
export default function AuthFooterLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a href={href} className="border-b border-smu-navy/25 font-semibold text-smu-navy transition-colors hover:border-smu-navy">
      {children}
    </a>
  )
}
