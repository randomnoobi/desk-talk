import { act, renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { usePoll } from './usePoll'

describe('usePoll', () => {
  it('keeps the last good data when a call fails, and says what went wrong', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(1000)
    const load = vi.fn<() => Promise<{ n: number }>>()
      .mockResolvedValueOnce({ n: 1 })
      .mockRejectedValueOnce(new Error('no answer'))
    const { result } = renderHook(() => usePoll(load, 500))

    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(result.current).toMatchObject({ data: { n: 1 }, error: null, lastOk: 1000 })

    await act(async () => { await vi.advanceTimersByTimeAsync(500) })
    expect(load).toHaveBeenCalledTimes(2)
    expect(result.current).toMatchObject({ data: { n: 1 }, error: 'no answer', lastOk: 1000 })
  })

  it('clears the error once a call works again', async () => {
    vi.useFakeTimers()
    const load = vi.fn<() => Promise<number>>()
      .mockRejectedValueOnce(new Error('no answer'))
      .mockResolvedValueOnce(2)
    const { result } = renderHook(() => usePoll(load, 500))

    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    expect(result.current).toMatchObject({ data: null, error: 'no answer', lastOk: null })

    await act(async () => { await vi.advanceTimersByTimeAsync(500) })
    expect(result.current).toMatchObject({ data: 2, error: null })
  })

  it('stops asking once the page is gone', async () => {
    vi.useFakeTimers()
    const load = vi.fn<() => Promise<number>>().mockResolvedValue(1)
    const { unmount } = renderHook(() => usePoll(load, 500))
    await act(async () => { await vi.advanceTimersByTimeAsync(0) })
    unmount()
    await vi.advanceTimersByTimeAsync(5000)
    expect(load).toHaveBeenCalledTimes(1)
  })
})
