import { useEffect } from 'react'
import type { RefObject } from 'react'

/** Calls onOutside on any pointerdown outside the ref'd element, but only while `active`.
 *  Used to close a dropdown when the user clicks elsewhere on the page. */
export function useClickOutside(ref: RefObject<HTMLElement>, active: boolean, onOutside: () => void) {
  useEffect(() => {
    if (!active) return
    function handle(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onOutside()
    }
    document.addEventListener('mousedown', handle)
    return () => document.removeEventListener('mousedown', handle)
  }, [active, onOutside, ref])
}
