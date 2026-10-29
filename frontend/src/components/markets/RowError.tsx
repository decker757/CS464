import Button from '../ui/Button'

interface RowErrorProps {
  message: string
  onReload?: () => void
}

// A refused action's message on an admin markets row, with Reload when the
// refusal means the row is out of date.
export default function RowError({ message, onReload }: RowErrorProps) {
  return (
    <div role="alert" className="mt-1 flex items-center gap-2 text-xs text-danger">
      <span>{message}</span>
      {onReload && (
        <Button variant="outline" size="xs" onClick={onReload}>
          Reload
        </Button>
      )}
    </div>
  )
}
