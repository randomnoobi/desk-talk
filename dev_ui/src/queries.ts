// Ready-made queries for the Database tab. They read the `events` table that
// desktalk/adapters/storage/object_states_db.py writes; if its columns change, change these too.
// Times are shown on this computer's clock. The server stops at settings.sql_max_rows rows, so
// these don't need a LIMIT of their own. How far back the flips query looks is settings.flips_window_min.
import type { DevSettings } from './api'

/** The database the ready-made queries are for (the name bootstrap gives SqliteBrowser). */
export const OBJECT_STATES_DB = 'object_states'

export interface ReadyMade {
  name: string
  sql: string
}

/** The ready-made queries, with the numbers they need from the settings filled in. */
export function readyMade(settings: Pick<DevSettings, 'flips_window_min'>): ReadyMade[] {
  const minutes = Number(settings.flips_window_min)
  return [
    {
      name: 'Latest changes',
      sql: `SELECT datetime(ts, 'unixepoch', 'localtime') AS at, object, type, prev_state, new_state, conf_avg, dwell_sec
FROM events
WHERE type IN ('appeared', 'removed', 'moved')
ORDER BY ts DESC, id DESC`,
    },
    {
      name: `Flips per object, last ${minutes === 1 ? 'minute' : `${minutes} minutes`}`,
      sql: `SELECT object, COUNT(*) AS flips,
       SUM(type = 'appeared') AS appeared, SUM(type = 'removed') AS removed,
       datetime(MAX(ts), 'unixepoch', 'localtime') AS last_flip
FROM events
WHERE type IN ('appeared', 'removed')
  AND ts > CAST(strftime('%s', 'now') AS REAL) - ${minutes} * 60
GROUP BY object
ORDER BY flips DESC, object`,
    },
    {
      name: "Each object's newest state",
      sql: `SELECT object, new_state AS state, type AS from_row, datetime(ts, 'unixepoch', 'localtime') AS at
FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY object ORDER BY ts DESC, id DESC) AS newest
      FROM events
      WHERE type IN ('appeared', 'removed', 'snapshot'))
WHERE newest = 1
ORDER BY object`,
    },
  ]
}
