import Card from './Card'

interface SectionCardProps {
  title: string
  className?: string
  children: React.ReactNode
}

/** A Card with a titled header, for one section of a page. */
export default function SectionCard({ title, className = '', children }: SectionCardProps) {
  return (
    <Card className={`px-7 py-6 ${className}`}>
      <h2 className="mb-5 border-b border-smu-gold/12 pb-3 text-[15px] font-bold text-smu-navy">{title}</h2>
      {children}
    </Card>
  )
}
