import AppNavbar from '../AppNavbar'

interface AppLayoutProps {
  /** The content's max width, e.g. `max-w-[900px]`. */
  width: string
  children: React.ReactNode
}

/** The frame of every signed-in page: navbar, cream background, centred content. */
export default function AppLayout({ width, children }: AppLayoutProps) {
  return (
    <div className="min-h-screen bg-smu-cream">
      <AppNavbar />
      <main className={`mx-auto px-8 py-12 ${width}`}>{children}</main>
    </div>
  )
}
