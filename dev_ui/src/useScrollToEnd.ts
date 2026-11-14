import { useCallback, useEffect, useRef, type DependencyList, type RefObject, type UIEvent } from 'react'

// How close to the end still counts as "at the end": this share of the box's own height. It is about
// how the box feels to scroll, not a setting of the app, so it lives here rather than in settings.yaml.
const NEAR_END_SHARE = 1 / 4

/**
 * Keeps a scrolling box at its end each time `deps` change, unless you've scrolled up to read something.
 * Scrolling back down near the end makes it follow again.
 * Example: const end = useScrollToEnd<HTMLPreElement>([output]); <pre ref={end.ref} onScroll={end.onScroll}>
 */
export function useScrollToEnd<T extends HTMLElement>(deps: DependencyList): {
  ref: RefObject<T>
  onScroll: (e: UIEvent<T>) => void
} {
  const ref = useRef<T>(null)
  const atEnd = useRef(true)
  useEffect(() => {
    const el = ref.current
    if (el && atEnd.current) el.scrollTop = el.scrollHeight
  }, deps)                    // the caller says what to follow
  const onScroll = useCallback((e: UIEvent<T>) => {
    const el = e.currentTarget
    atEnd.current = el.scrollHeight - el.scrollTop - el.clientHeight < el.clientHeight * NEAR_END_SHARE
  }, [])
  return { ref, onScroll }
}
