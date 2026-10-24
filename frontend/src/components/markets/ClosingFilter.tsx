import FilterPill from '../ui/FilterPill'
import { CLOSING_PRESETS, closingLabel, type ClosingPreset } from '../../utils/closingWindow'

interface ClosingFilterProps {
  closing: ClosingPreset | null
  onClosingChange: (closing: ClosingPreset | null) => void
}

// null stands for "Any time", the choice that sends no window.
const CLOSING_OPTIONS: (ClosingPreset | null)[] = [null, ...CLOSING_PRESETS]

// The keys that move a radio group's choice, and which way they move it.
const STEP_OF_KEY: Record<string, number> = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }

// One choice at a time, so a radio group rather than toggle buttons (#442
// point 2 is that defect on the status pills): only the checked pill is in
// the tab order, and the arrow keys move the choice and the focus together.
export default function ClosingFilter({ closing, onClosingChange }: ClosingFilterProps) {
  const checkedIndex = CLOSING_OPTIONS.indexOf(closing)

  function handleKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    const step = STEP_OF_KEY[event.key]
    if (step === undefined) return
    event.preventDefault()

    const nextIndex = (checkedIndex + step + CLOSING_OPTIONS.length) % CLOSING_OPTIONS.length
    onClosingChange(CLOSING_OPTIONS[nextIndex])
    event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="radio"]')[nextIndex]?.focus()
  }

  return (
    <div role="radiogroup" aria-label="Filter by closing time" onKeyDown={handleKeyDown} className="flex flex-wrap items-center gap-2">
      <span aria-hidden="true" className="text-xs font-semibold text-muted">Closing</span>
      {CLOSING_OPTIONS.map(option => (
        <FilterPill
          key={option ?? 'any'}
          role="radio"
          active={closing === option}
          aria-checked={closing === option}
          tabIndex={closing === option ? 0 : -1}
          onClick={() => onClosingChange(option)}
        >
          {closingLabel(option)}
        </FilterPill>
      ))}
    </div>
  )
}
