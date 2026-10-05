import Button from '../ui/Button'

interface LoadMoreMarketsProps {
  isLoading: boolean
  hasFailed: boolean
  onLoadMore: () => void
}

// The control under a paged market list ([X-1] #104, [2.1] #235): why the last
// attempt failed, if it did, and the button that asks for the next page.
export default function LoadMoreMarkets({ isLoading, hasFailed, onLoadMore }: LoadMoreMarketsProps) {
  return (
    <>
      {hasFailed && (
        <p role="alert" className="mt-6 text-sm text-danger">Couldn't load more markets. Please try again.</p>
      )}
      <div className="mt-8 flex justify-center">
        {/* Disabled while a page is on its way, so a double click asks for it once (#104). */}
        <Button variant="outline" size="md" onClick={onLoadMore} disabled={isLoading}>
          {isLoading ? 'Loading…' : 'Load more'}
        </Button>
      </div>
    </>
  )
}
