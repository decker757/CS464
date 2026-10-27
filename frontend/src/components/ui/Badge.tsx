export type Tone = 'success' | 'neutral' | 'warning' | 'info' | 'danger'

// The tint stays; the text is one step darker than the tone it sits on. A tone
// over its own 10% tint reaches only about 4.4:1, and this is 11px. [#360]
const TONES: Record<Tone, string> = {
  success: 'border-success/25 bg-success/10 text-success-strong',
  neutral: 'border-muted/25 bg-muted/10 text-secondary',
  warning: 'border-warning/25 bg-warning/10 text-warning-strong',
  info: 'border-info/25 bg-info/10 text-info-strong',
  danger: 'border-danger/25 bg-danger/10 text-danger-strong',
}

interface BadgeProps {
  tone: Tone
  /**
   * Lets a long label wrap inside the pill instead of holding it to one line.
   * A status is a word or two and never needs it; a badge carrying an author's
   * own text — an outcome label runs to 120 characters — does, or one long
   * label pushes a card wider than a phone (#516).
   */
  wraps?: boolean
  children: React.ReactNode
}

/** A small uppercase pill, for a status or a short label. */
export default function Badge({ tone, wraps = false, children }: BadgeProps) {
  const fit = wraps ? 'min-w-0 break-words' : 'shrink-0'
  return (
    <span className={`inline-block ${fit} rounded-full border px-2.5 py-[3px] text-[11px] font-bold tracking-[0.5px] uppercase ${TONES[tone]}`}>
      {children}
    </span>
  )
}
