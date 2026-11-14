import { fireEvent, render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { useScrollToEnd } from './useScrollToEnd'

function Box(props: { text: string }): JSX.Element {
  const end = useScrollToEnd<HTMLDivElement>([props.text])
  return <div ref={end.ref} onScroll={end.onScroll} data-testid="box">{props.text}</div>
}

/** jsdom lays nothing out, so give the box its sizes by hand. */
function sizeBox(el: HTMLElement, sizes: { scrollHeight: number, clientHeight: number }) {
  Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => sizes.scrollHeight })
  Object.defineProperty(el, 'clientHeight', { configurable: true, get: () => sizes.clientHeight })
}

describe('useScrollToEnd', () => {
  it('scrolls to the end when the text grows', () => {
    const view = render(<Box text="a" />)
    const box = view.getByTestId('box')
    sizeBox(box, { scrollHeight: 1000, clientHeight: 100 })
    view.rerender(<Box text="ab" />)
    expect(box.scrollTop).toBe(1000)
  })

  it('stays put after you scroll up, and follows again once you scroll back near the end', () => {
    const view = render(<Box text="a" />)
    const box = view.getByTestId('box')
    sizeBox(box, { scrollHeight: 1000, clientHeight: 100 })

    box.scrollTop = 200                         // far from the end
    fireEvent.scroll(box)
    view.rerender(<Box text="ab" />)
    expect(box.scrollTop).toBe(200)

    box.scrollTop = 880                         // 20 from the end: less than a quarter of the box
    fireEvent.scroll(box)
    view.rerender(<Box text="abc" />)
    expect(box.scrollTop).toBe(1000)
  })
})
