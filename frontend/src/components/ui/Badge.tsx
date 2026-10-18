export type Tone = 'success' | 'neutral' | 'warning' | 'info'

// The tint stays; the text is one step darker than the tone it sits on. A tone
// over its own 10% tint reaches only about 4.4:1, and this is 11px. [#360]
const TONES: Record<Tone, string> = {
  success: 'border-success/25 bg-success/10 text-success-strong',
  neutral: 'border-muted/25 bg-muted/10 text-secondary',
  warning: 'border-warning/25 bg-warning/10 text-warning-strong',
  info: 'border-info/25 bg-info/10 text-info-strong',
}

/** A small uppercase pill for a status. */
export default function Badge({ tone, children }: { tone: Tone; children: React.ReactNode }) {
  return (
    <span className={`inline-block shrink-0 rounded-full border px-2.5 py-[3px] text-[11px] font-bold tracking-[0.5px] uppercase ${TONES[tone]}`}>
      {children}
    </span>
  )
}
