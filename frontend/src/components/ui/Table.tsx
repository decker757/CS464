import type { ReactNode } from 'react'

/** The rounded, bordered card every data table sits in. */
export function TableFrame({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-x-auto rounded-2xl border border-smu-gold/15 bg-white shadow-card">
      <table className="w-full text-left text-sm">{children}</table>
    </div>
  )
}

/** A right-aligned table cell for a plain number or price. */
export function NumberCell({ children }: { children: ReactNode }) {
  return <td className="px-5 py-3 text-right text-smu-navy">{children}</td>
}

/** The header row every data table opens with: a row of HeaderCells. */
export function TableHeaderRow({ children }: { children: ReactNode }) {
  return (
    <tr className="border-b border-line text-xs font-semibold tracking-[0.5px] text-subtle uppercase">
      {children}
    </tr>
  )
}

export function HeaderCell({ align = 'left', children }: { align?: 'left' | 'right'; children: ReactNode }) {
  return <th className={`px-5 py-3 ${align === 'right' ? 'text-right' : ''}`}>{children}</th>
}
