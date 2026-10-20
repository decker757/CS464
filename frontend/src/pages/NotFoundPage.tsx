import { Link } from 'react-router-dom'
import NoticePanel from '../components/layout/NoticePanel'
import { buttonClass } from '../components/ui/buttonClass'
import { useHomePath } from '../hooks/useHomePath'

/**
 * Whatever the address bar holds that no route declares: a typo, or a
 * bookmark to a page that has since moved ([FE] #355). It is open to guests
 * and signed-in users alike, inside the same frame as every other page, and
 * its way back is the wordmark's: the markets once signed in, the landing
 * page otherwise.
 */
export default function NotFoundPage() {
  const home = useHomePath()

  return (
    <NoticePanel
      title="Page not found"
      message="There is no page at this address. The link may be mistyped, or the page may have moved."
      action={
        <Link to={home} className={buttonClass('primary', 'md')}>
          Go to the home page
        </Link>
      }
    />
  )
}
