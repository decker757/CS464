import RadioPillGroup, { type RadioPillOption } from '../ui/RadioPillGroup'
import { CLOSING_PRESETS, closingLabel, type ClosingPreset } from '../../utils/closingWindow'

interface ClosingFilterProps {
  closing: ClosingPreset | null
  onClosingChange: (closing: ClosingPreset | null) => void
}

// null stands for "Any time", the choice that sends no window.
const CLOSING_OPTIONS: RadioPillOption<ClosingPreset | null>[] = [null, ...CLOSING_PRESETS].map(preset => ({
  value: preset,
  label: closingLabel(preset),
}))

// One choice at a time, so a radio group rather than toggle buttons, the
// same pattern as the status pills (#442).
export default function ClosingFilter({ closing, onClosingChange }: ClosingFilterProps) {
  return (
    <RadioPillGroup
      label="Filter by closing time"
      caption="Closing"
      options={CLOSING_OPTIONS}
      selected={closing}
      onSelect={onClosingChange}
    />
  )
}
