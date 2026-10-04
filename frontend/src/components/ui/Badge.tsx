export type Tone = 'success' | 'neutral' | 'warning' | 'info'

const TONES: Record<Tone, string> = {
  success: 'border-success/25 bg-success/10 text-success',
  neutral: 'border-muted/25 bg-muted/10 text-muted',
  warning: 'border-warning/25 bg-warning/10 text-warning',
  info: 'border-info/25 bg-info/10 text-info',
}

/** A small uppercase pill for a status. */
export default function Badge({ tone, children }: { tone: Tone; children: React.ReactNode }) {
  return (
    <span className={`inline-block shrink-0 rounded-full border px-2.5 py-[3px] text-[11px] font-bold tracking-[0.5px] uppercase ${TONES[tone]}`}>
      {children}
    </span>
  )
}
