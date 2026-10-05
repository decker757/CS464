import type { ReactNode } from 'react'

/** A right-aligned table cell for a plain number or price. */
export function NumberCell({ children }: { children: ReactNode }) {
  return <td className="px-5 py-3 text-right text-smu-navy">{children}</td>
}

export function HeaderCell({ align = 'left', children }: { align?: 'left' | 'right'; children: ReactNode }) {
  return <th className={`px-5 py-3 ${align === 'right' ? 'text-right' : ''}`}>{children}</th>
}
