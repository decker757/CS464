import Logo from '../ui/Logo'

/** The navy bar across the top of every page: the logo, then whatever controls the page passes. */
export default function TopBar({ children }: { children?: React.ReactNode }) {
  return (
    <header className="bg-smu-navy">
      <nav className="mx-auto flex h-[72px] max-w-[1200px] items-center justify-between px-8">
        <Logo className="text-xl text-white" />
        <div className="flex items-center gap-3">{children}</div>
      </nav>
    </header>
  )
}
