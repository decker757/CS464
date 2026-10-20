import { Component } from 'react'
import { useLocation } from 'react-router-dom'
import AppLayout from './layout/AppLayout'
import PageTitle from './ui/PageTitle'
import { buttonClass } from './ui/buttonClass'

interface ErrorBoundaryProps {
  children: React.ReactNode
}

interface FaultBoundaryProps {
  /** Changes on every navigation, so a change while the panel is up clears it. */
  locationKey: string
  /** The address that failed, which "Reload the page" loads again. */
  address: string
  children: React.ReactNode
}

interface FaultBoundaryState {
  hasFailed: boolean
}

/**
 * React offers no hook that catches a render error, so this one part has to
 * be a class. It is kept private: `ErrorBoundary` below is the component, and
 * hands it the two things it needs from the router.
 */
class FaultBoundary extends Component<FaultBoundaryProps, FaultBoundaryState> {
  state: FaultBoundaryState = { hasFailed: false }

  // Only *that* it failed. The error itself is never put on screen: React
  // has already logged it, stack and all, to the console for a developer, and
  // a user can do nothing with it.
  static getDerivedStateFromError(): FaultBoundaryState {
    return { hasFailed: true }
  }

  // Cleared when the address changes, rather than keyed on it. A key would
  // remount every page on every navigation, and the create form changes its
  // own address under a form that has to stay mounted (#348).
  componentDidUpdate(previous: FaultBoundaryProps) {
    if (this.state.hasFailed && previous.locationKey !== this.props.locationKey) {
      this.setState({ hasFailed: false })
    }
  }

  render() {
    if (!this.state.hasFailed) return this.props.children

    // The page that threw took its own navbar down with it, since every page
    // draws its own frame. The panel draws it again, which is why this has
    // to sit inside AuthProvider and BalanceProvider (#367's review).
    return (
      <AppLayout width="max-w-[640px]">
        <PageTitle className="mb-3">Something went wrong</PageTitle>
        <p role="alert" className="mb-8 text-muted">
          This page hit an error and could not be shown. Reload it to try again, or use the
          navigation above to go somewhere else.
        </p>
        {/* A plain link, not the router's: loading the same address from the
            server is the reload, and starts the app afresh. */}
        <a href={this.props.address} className={buttonClass('primary', 'md')}>
          Reload the page
        </a>
      </AppLayout>
    )
  }
}

/**
 * Turns a render error anywhere below it into a recovery panel inside the app
 * frame, instead of an empty white page ([FE] #355).
 */
export default function ErrorBoundary({ children }: ErrorBoundaryProps) {
  const location = useLocation()
  const address = `${location.pathname}${location.search}${location.hash}`

  return (
    <FaultBoundary locationKey={location.key} address={address}>
      {children}
    </FaultBoundary>
  )
}
