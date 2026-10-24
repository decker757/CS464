import { useRef, type KeyboardEvent } from 'react'
import FilterPill from './FilterPill'

export interface RadioPillOption<Value> {
  value: Value
  label: string
}

interface RadioPillGroupProps<Value> {
  /** The group's accessible name, such as "Filter by status". */
  label: string
  /**
   * A short word shown before the pills, such as "Closing". It is for the eye
   * only: the group's name is still `label`, so a screen reader hears that
   * once rather than both.
   */
  caption?: string
  options: RadioPillOption<Value>[]
  selected: Value
  onSelect: (value: Value) => void
}

// The arrow keys of WAI-ARIA's radio group pattern: Right and Down choose the
// next option, Left and Up the previous one, wrapping at either end. Any other
// key is not the group's, so the answer is null.
function arrowTargetIndex(key: string, index: number, count: number): number | null {
  if (key === 'ArrowRight' || key === 'ArrowDown') {
    return (index + 1) % count
  }
  if (key === 'ArrowLeft' || key === 'ArrowUp') {
    return (index - 1 + count) % count
  }
  return null
}

/**
 * A row of FilterPills of which exactly one is chosen, so a radio group
 * (#442, #479). Native radios would bring the arrow keys for free but not
 * FilterPill's look, so the pattern is written here: the arrows move the
 * choice and the focus together, and only the chosen pill is in the tab
 * order. preventDefault keeps the arrows from scrolling. An arrow with Alt,
 * Ctrl or Cmd held is the browser's or the screen reader's (Alt+Left is
 * Back), so the group leaves it alone.
 */
export default function RadioPillGroup<Value>({ label, caption, options, selected, onSelect }: RadioPillGroupProps<Value>) {
  const pillRefs = useRef<(HTMLButtonElement | null)[]>([])

  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (event.altKey || event.ctrlKey || event.metaKey) {
      return
    }
    const targetIndex = arrowTargetIndex(event.key, index, options.length)
    if (targetIndex === null) {
      return
    }
    event.preventDefault()
    onSelect(options[targetIndex].value)
    pillRefs.current[targetIndex]?.focus()
  }

  return (
    <div role="radiogroup" aria-label={label} className="flex flex-wrap items-center gap-2">
      {caption && (
        <span aria-hidden="true" className="text-xs font-semibold text-muted">
          {caption}
        </span>
      )}
      {options.map((option, index) => {
        const isChecked = selected === option.value
        return (
          <FilterPill
            key={option.label}
            ref={element => {
              pillRefs.current[index] = element
            }}
            active={isChecked}
            role="radio"
            aria-checked={isChecked}
            tabIndex={isChecked ? 0 : -1}
            onClick={() => onSelect(option.value)}
            onKeyDown={event => handleKeyDown(event, index)}
          >
            {option.label}
          </FilterPill>
        )
      })}
    </div>
  )
}
