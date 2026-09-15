import { AssistantRuntimeProvider, type ThreadMessage, useExternalStoreRuntime } from '@assistant-ui/react'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { Thread } from '.'

class TestResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
vi.stubGlobal('ResizeObserver', TestResizeObserver)
vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) =>
  window.setTimeout(() => callback(performance.now()), 0)
)
vi.stubGlobal('cancelAnimationFrame', (id: number) => window.clearTimeout(id))
vi.stubGlobal('CSS', { escape: (str: string) => str })

Element.prototype.scrollTo = function scrollTo() {}

afterEach(() => {
  cleanup()
})

function Harness({ messages }: { messages: ThreadMessage[] }) {
  const runtime = useExternalStoreRuntime<ThreadMessage>({
    messages,
    isRunning: false,
    onNew: async () => {}
  })

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <Thread />
    </AssistantRuntimeProvider>
  )
}

describe('empty thread viewport', () => {
  it('skips stick-to-bottom on an empty transcript so workspace cannot loop', () => {
    const { container } = render(<Harness messages={[]} />)
    const empty = container.querySelector('[data-thread-empty="true"]')

    expect(empty).toBeTruthy()
    expect(container.querySelector('[data-following]')).toBeNull()
  })

  it('mounts stick-to-bottom once a message exists', async () => {
    const createdAt = new Date('2026-05-01T00:00:00.000Z')
    const { container } = render(
      <Harness
        messages={[
          {
            id: 'u1',
            role: 'user',
            content: [{ type: 'text', text: 'hi' }],
            attachments: [],
            createdAt,
            metadata: { custom: {} }
          } as ThreadMessage
        ]}
      />
    )

    expect(await screen.findByText('hi')).toBeTruthy()
    expect(container.querySelector('[data-thread-empty="true"]')).toBeNull()
    expect(container.querySelector('[data-slot="aui_thread-viewport"]')).toBeTruthy()
  })
})
