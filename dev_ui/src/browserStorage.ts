// What the app page (web_app/app.js) keeps in this browser's localStorage, read for the Storage tab.
// The developer page and the app page share it only when both come from the same address
// (http://localhost:<web.port>, the port in settings → web.port), because a browser keeps one
// localStorage per address.

/** The keys web_app/app.js uses, and what each one is for. */
export const APP_KEYS: Readonly<Record<string, string>> = {
  desktalk_chats_v2: 'Chat history',
  desktalk_todo_v1: "Paper's to-do list",
  desktalk_habits_v2: 'Habit counters',
}

const DESK_TALK_PREFIX = 'desktalk_'
export const UNKNOWN_DESK_TALK_KEY = 'Desk Talk (unknown key)'
export const NOT_DESK_TALK = 'Not Desk Talk'

/** One localStorage key. `json` is the parsed value, or null when it isn't JSON. */
export interface BrowserKey {
  key: string
  bytes: number                    // the value's size in UTF-8
  value: string
  json: unknown | null
  usedBy: string
}

/** What uses this key, in words. */
export function usedBy(key: string): string {
  if (key in APP_KEYS) return APP_KEYS[key]
  return key.startsWith(DESK_TALK_PREFIX) ? UNKNOWN_DESK_TALK_KEY : NOT_DESK_TALK
}

function parseJSON(value: string): unknown | null {
  try {
    return JSON.parse(value)
  } catch {
    return null
  }
}

/** Every key in `store`, sorted by name. Gives [] when the browser won't let us read it. */
export function readBrowserStorage(store: Storage): BrowserKey[] {
  try {
    const keys: string[] = []
    for (let i = 0; i < store.length; i += 1) {
      const key = store.key(i)
      if (key !== null) keys.push(key)
    }
    const encoder = new TextEncoder()
    return keys.sort().flatMap(key => {
      const value = store.getItem(key)
      if (value === null) return []           // removed while we were reading
      return [{ key, bytes: encoder.encode(value).length, value, json: parseJSON(value), usedBy: usedBy(key) }]
    })
  } catch {
    return []
  }
}

/** The question asked before deleting a key. */
export function deleteQuestion(row: Pick<BrowserKey, 'key' | 'usedBy'>): string {
  if (row.key in APP_KEYS) return `Delete ${row.key}? The app page loses ${row.usedBy}.`
  return `Delete ${row.key}? This page doesn't know what uses it (${row.usedBy}).`
}

/** Removes one key. Gives null when it worked, or what went wrong. */
export function deleteBrowserKey(store: Storage, key: string): string | null {
  try {
    store.removeItem(key)
    return null
  } catch (e) {
    return e instanceof Error ? e.message : String(e)
  }
}

/** The parts of window.location this needs. */
export interface PageAddress {
  origin: string                   // e.g. http://localhost:5000
  protocol: string                 // e.g. http:
  hostname: string                 // e.g. localhost
}

/**
 * Whether this page sees the app page's storage, and words saying so.
 * `fromViteDev` is true when `npm run dev` sent this page rather than the Desk Talk server.
 * Then the words give the Desk Talk server's own address, using `webPort` (settings → web.port).
 */
export function whereThisPageIs(page: PageAddress, fromViteDev: boolean,
                                webPort: number): { sharesWithApp: boolean, words: string } {
  if (fromViteDev) {
    return {
      sharesWithApp: false,
      words: `This page is coming from npm run dev at ${page.origin}, a different address from the app page, ` +
        "so this isn't the app's storage. To see the app's storage, open this page from the Desk Talk server: " +
        `${page.protocol}//${page.hostname}:${webPort}/dev/`,
    }
  }
  return {
    sharesWithApp: true,
    words: `This page and the app page at ${page.origin}/ share this storage. ` +
      'The app page opened at another address (127.0.0.1 instead of localhost, say) keeps its own.',
  }
}
