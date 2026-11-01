import { describe, expect, it, vi } from 'vitest'
import { isSeen, markSeen } from './seen'

describe('seen reports', () => {
  it('remembers a report in this browser', () => {
    expect(isSeen('a')).toBe(false)
    markSeen('a')
    expect(isSeen('a')).toBe(true)
    expect(isSeen('b')).toBe(false)
  })

  it('keeps working when storage is blocked', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new DOMException('blocked', 'SecurityError') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new DOMException('blocked', 'SecurityError') })
    markSeen('c')
    expect(isSeen('c')).toBe(true)
  })

  it('ignores a damaged saved list', () => {
    window.localStorage.setItem('desktalk_cloud_seen_reports', '{not json')
    expect(isSeen('d')).toBe(false)
    markSeen('d')
    expect(isSeen('d')).toBe(true)
  })
})
