import { useEffect, type RefObject } from 'react'

/**
 * Calls `onClose` when a mousedown lands outside `ref` while `isOpen` is true.
 * The navbar's account menu and its navigation menu both close this way
 * (#437, #438), so there is one implementation for the two of them.
 *
 * Wrap the button that opens the panel in `ref` as well as the panel. A
 * click on that button is then not "elsewhere": it closes the panel through
 * the button's own toggle, instead of being closed here and reopened by the
 * click that follows.
 */
export function useCloseOnClickElsewhere(ref: RefObject<HTMLElement | null>, isOpen: boolean, onClose: () => void): void {
  useEffect(() => {
    if (!isOpen) return
    const closeOnClickElsewhere = (event: MouseEvent) => {
      const container = ref.current
      if (container && event.target instanceof Node && !container.contains(event.target)) onClose()
    }
    document.addEventListener('mousedown', closeOnClickElsewhere)
    return () => document.removeEventListener('mousedown', closeOnClickElsewhere)
  }, [ref, isOpen, onClose])
}
