import { useCallback, useEffect, useRef, useState } from 'react'

export interface Poll<T> {
  data: T | null           // the last reply that worked (kept when a later call fails)
  error: string | null     // what went wrong with the latest call, or null when it worked
  lastOk: number | null    // when a call last worked (Date.now() milliseconds)
  refresh: () => void      // ask again now, e.g. after picking another object
}

/**
 * Calls `load` now and then again `everyMs` after each answer, until the component goes away.
 * A call never starts while the one before is still waiting. The newest `load` is always the one
 * used, so it can be an inline function; call `refresh()` to use a changed one at once.
 */
export function usePoll<T>(load: () => Promise<T>, everyMs: number): Poll<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [lastOk, setLastOk] = useState<number | null>(null)
  const loadRef = useRef(load)
  loadRef.current = load
  const kick = useRef<() => void>(() => {})

  useEffect(() => {
    let gone = false
    let timer: ReturnType<typeof setTimeout> | undefined
    let busy = false
    let again = false

    const run = async () => {
      if (busy) {
        again = true             // a refresh while waiting: ask once more when this answer is in
        return
      }
      clearTimeout(timer)
      busy = true
      try {
        const reply = await loadRef.current()
        if (gone) return
        setData(reply)
        setError(null)
        setLastOk(Date.now())
      } catch (e) {
        if (gone) return
        setError(e instanceof Error ? e.message : String(e))
      } finally {
        busy = false
      }
      if (gone) return
      if (again) {
        again = false
        void run()
      } else {
        timer = setTimeout(run, everyMs)
      }
    }

    kick.current = () => { void run() }
    void run()
    return () => {
      gone = true
      clearTimeout(timer)
    }
  }, [everyMs])

  const refresh = useCallback(() => kick.current(), [])
  return { data, error, lastOk, refresh }
}
