/** The white, gold-bordered panel used across the app. Pass padding and margin in `className`. */
export default function Card({ className = '', ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div {...props} className={`rounded-2xl border border-smu-gold/15 bg-white shadow-card ${className}`} />
}
