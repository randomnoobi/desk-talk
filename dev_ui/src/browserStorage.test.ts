import { describe, expect, it, vi } from 'vitest'
import {
  deleteBrowserKey, deleteQuestion, PI_LOG_SOURCE_KEY, readBrowserStorage, readChoice, saveChoice, usedBy, whereThisPageIs,
} from './browserStorage'

/** A Storage that keeps its keys in a Map, like a browser's localStorage. */
class FakeStorage implements Storage {
  [name: string]: unknown
  private readonly items = new Map<string, string>()

  constructor(start: Record<string, string> = {}) {
    for (const [k, v] of Object.entries(start)) this.items.set(k, v)
  }

  get length(): number { return this.items.size }
  key(i: number): string | null { return [...this.items.keys()][i] ?? null }
  getItem(key: string): string | null { return this.items.get(key) ?? null }
  setItem(key: string, value: string): void { this.items.set(key, value) }
  removeItem(key: string): void { this.items.delete(key) }
  clear(): void { this.items.clear() }
}

/** A Storage the browser won't let us read (a private window, say). */
class BlockedStorage extends FakeStorage {
  getItem(): string | null { throw new DOMException('The operation is insecure.', 'SecurityError') }
}

describe('readBrowserStorage', () => {
  it("names Paper's to-do list and reads its JSON", () => {
    const todo = { type: 'todo', items: [{ text: 'Write the storage tab', done: false }] }
    const rows = readBrowserStorage(new FakeStorage({ desktalk_todo_v1: JSON.stringify(todo) }))
    expect(rows).toHaveLength(1)
    expect(rows[0].key).toBe('desktalk_todo_v1')
    expect(rows[0].usedBy).toBe("Paper's to-do list")
    expect(rows[0].json).toEqual(todo)
    expect(rows[0].value).toBe(JSON.stringify(todo))
    expect(rows[0].bytes).toBe(new TextEncoder().encode(JSON.stringify(todo)).length)
  })

  it('gives json null for a value that is not JSON', () => {
    const rows = readBrowserStorage(new FakeStorage({ desktalk_habits_v2: '{not json' }))
    expect(rows[0].json).toBeNull()
    expect(rows[0].value).toBe('{not json')
    expect(rows[0].usedBy).toBe('Habit counters')
  })

  it('gives [] when the browser won\'t let us read', () => {
    expect(readBrowserStorage(new BlockedStorage({ desktalk_chats_v2: '{}' }))).toEqual([])
  })

  it('lists the keys in order, and counts bytes, not letters', () => {
    const rows = readBrowserStorage(new FakeStorage({ zebra: 'é', desktalk_chats_v2: '{}' }))
    expect(rows.map(r => r.key)).toEqual(['desktalk_chats_v2', 'zebra'])
    expect(rows[1].bytes).toBe(2)
  })
})

describe('usedBy', () => {
  it('names each key the app page uses, and says when it does not know one', () => {
    expect(usedBy('desktalk_chats_v2')).toBe('Chat history')
    expect(usedBy('desktalk_todo_v1')).toBe("Paper's to-do list")
    expect(usedBy('desktalk_habits_v2')).toBe('Habit counters')
    expect(usedBy('desktalk_chats_v1')).toBe('Desk Talk (unknown key)')
    expect(usedBy('something_else')).toBe('Not Desk Talk')
  })

  it('names the keys this developer page keeps', () => {
    expect(usedBy(PI_LOG_SOURCE_KEY)).toBe('Developer page: the log the Pi log tab shows')
  })
})

describe('a choice the developer page remembers', () => {
  it('keeps it and gives it back', () => {
    localStorage.clear()
    expect(readChoice(PI_LOG_SOURCE_KEY)).toBeNull()
    saveChoice(PI_LOG_SOURCE_KEY, 'detector')
    expect(readChoice(PI_LOG_SOURCE_KEY)).toBe('detector')
    localStorage.clear()
  })

  it('gives null, and does not throw, when the browser will not let the page use its storage', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked') })
    expect(() => saveChoice(PI_LOG_SOURCE_KEY, 'detector')).not.toThrow()
    expect(readChoice(PI_LOG_SOURCE_KEY)).toBeNull()
  })
})

describe('deleting a key', () => {
  it('asks with what the app page loses', () => {
    const [row] = readBrowserStorage(new FakeStorage({ desktalk_todo_v1: '{}' }))
    expect(deleteQuestion(row)).toBe("Delete desktalk_todo_v1? The app page loses Paper's to-do list.")
  })

  it('says the developer page only forgets a choice for its own keys', () => {
    expect(deleteQuestion({ key: PI_LOG_SOURCE_KEY, usedBy: usedBy(PI_LOG_SOURCE_KEY) })).toBe(
      `Delete ${PI_LOG_SOURCE_KEY}? The developer page forgets this choice ` +
      '(Developer page: the log the Pi log tab shows).')
  })

  it('removes the key, and gives the reason when the browser says no', () => {
    const store = new FakeStorage({ desktalk_todo_v1: '{}', desktalk_habits_v2: '{}' })
    expect(deleteBrowserKey(store, 'desktalk_todo_v1')).toBeNull()
    expect(readBrowserStorage(store).map(r => r.key)).toEqual(['desktalk_habits_v2'])

    const stuck = new FakeStorage({ desktalk_todo_v1: '{}' })
    stuck.removeItem = () => { throw new Error('blocked') }
    expect(deleteBrowserKey(stuck, 'desktalk_todo_v1')).toBe('blocked')
  })
})

describe('whereThisPageIs', () => {
  it('says the page shares the app page\'s storage when the server sent it', () => {
    const where = whereThisPageIs({ origin: 'http://localhost:5000', protocol: 'http:', hostname: 'localhost' }, false, 5000)
    expect(where.sharesWithApp).toBe(true)
    expect(where.words).toContain('http://localhost:5000/')
  })

  it('says where to open it under npm run dev, using this address and settings → web.port', () => {
    const where = whereThisPageIs({ origin: 'http://localhost:6123', protocol: 'http:', hostname: 'localhost' }, true, 5123)
    expect(where.sharesWithApp).toBe(false)
    expect(where.words).toContain('http://localhost:6123')
    expect(where.words).toContain('http://localhost:5123/dev/')
    expect(where.words).not.toContain('<web.port>')
  })
})
