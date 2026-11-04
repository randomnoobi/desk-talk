// One weekly report, written as markdown. Raw HTML in it is never switched on, and links are made safe.
import { useEffect } from 'react'
import Markdown from 'react-markdown'
import { getReport } from '../api'
import { markSeen } from '../seen'
import { Loading, Problem } from '../ui'
import { useLoad } from '../useLoad'
import { statusWords } from './Reports'

export default function OneReport({ id }: { id: string }) {
  const { data, error, loading } = useLoad(() => getReport(id), id)

  useEffect(() => {
    if (data && data.status === 'done') markSeen(data.id)
  }, [data])

  if (error) return <section><Problem error={error} /><p><a href="#/reports">All reports</a></p></section>
  if (!data) return loading ? <Loading /> : null
  const note = statusWords(data)

  return (
    <section className="stack">
      <p><a href="#/reports">All reports</a></p>
      <h2>Week of {data.week_start}</h2>
      {note && <p className={data.status === 'failed' ? 'error' : 'muted'}>{note}</p>}
      {data.status === 'failed' && data.error && <p>{data.error}</p>}
      {data.status === 'done' && data.markdown !== null && (
        <article className="report">
          <Markdown
            components={{
              // links open in a new tab and tell the new page nothing about this one
              a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer nofollow" />,
              // a picture would load from somewhere else when the page opens: show its words instead
              img: ({ alt }) => <span>{alt}</span>,
            }}
          >
            {data.markdown}
          </Markdown>
        </article>
      )}
    </section>
  )
}
