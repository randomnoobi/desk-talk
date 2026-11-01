// Loads something from the server when a page opens (and again when `key` changes or `reload` is called).
import { useCallback, useEffect, useState } from 'react'

export interface Loaded<T> {
  data: T | undefined
  error: Error | undefined
  loading: boolean
  reload: () => void
}

export function useLoad<T>(load: () => Promise<T>, key: unknown): Loaded<T> {
  const [state, setState] = useState<{ data?: T, error?: Error, loading: boolean }>({ loading: true })
  const [round, setRound] = useState(0)

  useEffect(() => {
    let current = true
    setState(before => ({ data: before.data, loading: true }))
    load().then(
      data => { if (current) setState({ data, loading: false }) },
      (error: unknown) => { if (current) setState({ error: error instanceof Error ? error : new Error(String(error)), loading: false }) },
    )
    return () => { current = false }
    // `load` is a new function on every draw; `key` and `round` say when to ask again
  }, [key, round]) // eslint-disable-line react-hooks/exhaustive-deps

  const reload = useCallback(() => setRound(n => n + 1), [])
  return { data: state.data, error: state.error, loading: state.loading, reload }
}
