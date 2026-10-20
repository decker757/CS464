import PageTitle from '../ui/PageTitle'
import AppLayout from './AppLayout'

interface NoticePanelProps {
  title: string
  message: string
  /** True when the message reports a failure, so a screen reader announces it. */
  isAlert?: boolean
  /** The one way on: a link styled with `buttonClass`. */
  action: React.ReactNode
}

/**
 * A page that is only a notice inside the app frame: a heading, a sentence and
 * one way on. The 404 page and the error boundary's recovery panel (#355).
 */
export default function NoticePanel({ title, message, isAlert = false, action }: NoticePanelProps) {
  return (
    <AppLayout width="max-w-[640px]">
      <PageTitle className="mb-3">{title}</PageTitle>
      <p role={isAlert ? 'alert' : undefined} className="mb-8 text-muted">
        {message}
      </p>
      {action}
    </AppLayout>
  )
}
