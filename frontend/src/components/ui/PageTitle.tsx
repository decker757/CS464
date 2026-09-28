interface PageTitleProps {
  children: React.ReactNode
  className?: string
}

/** The `<h1>` at the top of every signed-in page. Pass margin in `className`. */
export default function PageTitle({ children, className = '' }: PageTitleProps) {
  return <h1 className={`text-[28px] font-extrabold tracking-[-0.3px] text-smu-navy ${className}`}>{children}</h1>
}
