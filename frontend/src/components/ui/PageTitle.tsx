import { useDocumentTitle } from '../../hooks/useDocumentTitle'

interface PageTitleProps {
  children: React.ReactNode
  className?: string
}

/** The `<h1>` at the top of every signed-in page. Pass margin in `className`.
 *
 * It also names the browser tab (#356). Doing it here rather than in each
 * page means the tab and the heading are the same string by construction,
 * so they cannot drift apart later. Only a plain string can be a title; a
 * heading built from elements leaves the tab to whatever set it last, which
 * no page currently does. */
export default function PageTitle({ children, className = '' }: PageTitleProps) {
  useDocumentTitle(typeof children === 'string' ? children : null)

  return <h1 className={`text-[28px] font-extrabold tracking-[-0.3px] text-smu-navy ${className}`}>{children}</h1>
}
