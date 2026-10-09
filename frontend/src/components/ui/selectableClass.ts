// The selected/unselected colours for a toggleable option — a trade side, an
// outcome choice, a status tab. Filled navy when selected, a faint outline
// otherwise. Shape (padding, radius, text size) stays with the caller, since
// a pill tab and a block outcome button are not the same shape.
export function selectableClass(selected: boolean) {
  return selected
    ? 'border-smu-navy bg-smu-navy text-white'
    : 'border-smu-navy/20 text-smu-navy hover:border-smu-navy/40'
}
