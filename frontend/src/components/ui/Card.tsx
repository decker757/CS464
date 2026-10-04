interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Lift the shadow on hover, for a card that is clickable. */
  interactive?: boolean
  /** Replaces the default gold border colour, e.g. `border-info/40`. */
  borderColor?: string
}

/** The white, gold-bordered panel used across the app. Pass padding and margin in `className`. */
export default function Card({ interactive = false, borderColor = 'border-smu-gold/15', className = '', ...props }: CardProps) {
  const hover = interactive ? 'transition hover:border-smu-gold/35 hover:shadow-card-hover' : ''
  return <div {...props} className={`rounded-2xl border ${borderColor} bg-white shadow-card ${hover} ${className}`} />
}
