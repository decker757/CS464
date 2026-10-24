import { useRef, type KeyboardEvent } from 'react'
import FilterPill from '../ui/FilterPill'
import { CLOSING_PRESETS, closingLabel, type ClosingPreset } from '../../utils/closingWindow'

interface ClosingFilterProps {
  closing: ClosingPreset | null
  onClosingChange: (closing: ClosingPreset | null) => void
}

// null stands for "Any time", the choice that sends no window.
const CLOSING_OPTIONS: (ClosingPreset | null)[] = [null, ...CLOSING_PRESETS]

// The arrow keys of WAI-ARIA's radio group pattern: Right and Down choose the
// next option, Left and Up the previous one, wrapping at either end. Any other
// key is not the group's, so the answer is null. MarketFilters' (#486), word
// for word, until #485's shared RadioPillGroup reaches dev and both use it.
function arrowTargetIndex(key: string, index: number, count: number): number | null {
  if (key === 'ArrowRight' || key === 'ArrowDown') {
    return (index + 1) % count
  }
  if (key === 'ArrowLeft' || key === 'ArrowUp') {
    return (index - 1 + count) % count
  }
  return null
}

// One choice at a time, so a radio group rather than toggle buttons, the
// same pattern as the status pills (#442): only the checked pill is in the
// tab order, and the arrow keys move the choice and the focus together.
// preventDefault keeps the arrows from scrolling. An arrow with Alt, Ctrl or
// Cmd held is the browser's or the screen reader's (Alt+Left is Back), so
// the group leaves it alone.
export default function ClosingFilter({ closing, onClosingChange }: ClosingFilterProps) {
  const pillRefs = useRef<(HTMLButtonElement | null)[]>([])

  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (event.altKey || event.ctrlKey || event.metaKey) {
      return
    }
    const targetIndex = arrowTargetIndex(event.key, index, CLOSING_OPTIONS.length)
    if (targetIndex === null) {
      return
    }
    event.preventDefault()
    onClosingChange(CLOSING_OPTIONS[targetIndex])
    pillRefs.current[targetIndex]?.focus()
  }

  return (
    <div role="radiogroup" aria-label="Filter by closing time" className="flex flex-wrap items-center gap-2">
      <span aria-hidden="true" className="text-xs font-semibold text-muted">Closing</span>
      {CLOSING_OPTIONS.map((option, index) => (
        <FilterPill
          key={option ?? 'any'}
          ref={element => {
            pillRefs.current[index] = element
          }}
          role="radio"
          active={closing === option}
          aria-checked={closing === option}
          tabIndex={closing === option ? 0 : -1}
          onClick={() => onClosingChange(option)}
          onKeyDown={event => handleKeyDown(event, index)}
        >
          {closingLabel(option)}
        </FilterPill>
      ))}
    </div>
  )
}
