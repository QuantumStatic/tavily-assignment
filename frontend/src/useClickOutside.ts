import { useEffect, useRef } from 'react'
import type { RefObject } from 'react'

/** Calls onOutside on any mousedown outside the ref'd element, but only while `active`.
 *  Used to close a dropdown when the user clicks elsewhere on the page. The callback is
 *  held in a ref so an inline `() => setOpen(false)` doesn't re-subscribe every render. */
export function useClickOutside(ref: RefObject<HTMLElement>, active: boolean, onOutside: () => void) {
  const onOutsideRef = useRef(onOutside)
  useEffect(() => { onOutsideRef.current = onOutside })
  useEffect(() => {
    if (!active) return
    function handle(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onOutsideRef.current()
    }
    document.addEventListener('mousedown', handle)
    return () => document.removeEventListener('mousedown', handle)
  }, [active, ref])
}
