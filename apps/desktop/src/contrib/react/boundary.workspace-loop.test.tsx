import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ContribBoundary } from './boundary'

const LOOP_MESSAGE =
  'Maximum update depth exceeded. The result of getSnapshot should be cached to avoid an infinite loop.'

// Each test is its own incident — step the clock past the boundary's
// same-incident dedupe window (module state survives between tests).
let clock = Date.now()

beforeEach(() => {
  sessionStorage.clear()
  localStorage.clear()
  vi.useFakeTimers()
  clock += 10_000
  vi.setSystemTime(clock)
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.restoreAllMocks()
})

function Boom(): null {
  throw new Error(LOOP_MESSAGE)
}

describe('ContribBoundary getSnapshot loop', () => {
  it('logs the workspace-loop tag and auto-remounts on the first crash', () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => {})

    sessionStorage.setItem(
      'hermesUsesLoop',
      JSON.stringify({ getSnapshot: 'fake-selector', flips: 51 })
    )

    // Keep throwing until the boundary has shown its fallback — React's own
    // concurrent-recovery retry renders children again before the boundary
    // catches, so a single throw would never reach ContribBoundary.
    let shouldThrow = true

    function BoomOnce() {
      if (shouldThrow) {
        throw new Error(LOOP_MESSAGE)
      }

      return <div>recovered</div>
    }

    render(
      <ContribBoundary id="workspace">
        <BoomOnce />
      </ContribBoundary>
    )

    expect(screen.getByText('Restoring the workspace…')).toBeTruthy()
    expect(
      error.mock.calls.some(args => String(args[0]).includes('[workspace-loop]'))
    ).toBe(true)
    expect(
      error.mock.calls.some(
        args => args[2] && typeof args[2] === 'object' && (args[2] as { snapshotLoop?: boolean }).snapshotLoop
      )
    ).toBe(true)

    shouldThrow = false
    act(() => {
      vi.advanceTimersByTime(500)
    })

    expect(screen.getByText('recovered')).toBeTruthy()
  })

  it('wipes persisted layout and reloads on the second crash', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    const reload = vi.fn()
    vi.stubGlobal('location', { ...window.location, reload })
    localStorage.setItem('hermes.desktop.layoutTree.v2', '{"stale":true}')
    localStorage.setItem('hermes.desktop.lastSessionId', 'sess-1')
    localStorage.setItem('hermes.desktop.composer.model', 'keep-me')
    // First rung already spent in this tab.
    sessionStorage.setItem(
      'hermes.desktop.workspaceLoopRecovery.v1',
      JSON.stringify({ n: 1, at: Date.now() })
    )

    render(
      <ContribBoundary id="workspace">
        <Boom />
      </ContribBoundary>
    )

    expect(screen.getByText('Resetting the layout and reloading…')).toBeTruthy()

    act(() => {
      vi.advanceTimersByTime(200)
    })

    expect(reload).toHaveBeenCalledTimes(1)
    expect(localStorage.getItem('hermes.desktop.layoutTree.v2')).toBeNull()
    expect(localStorage.getItem('hermes.desktop.lastSessionId')).toBeNull()
    expect(localStorage.getItem('hermes.desktop.composer.model')).toBe('keep-me')
    vi.unstubAllGlobals()
  })

  it('falls back to the manual buttons once both rungs are spent', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})
    sessionStorage.setItem(
      'hermes.desktop.workspaceLoopRecovery.v1',
      JSON.stringify({ n: 2, at: Date.now() })
    )
    sessionStorage.setItem('hermesUsesLoop', JSON.stringify({ getSnapshot: '() => store.get()', flips: 77 }))

    render(
      <ContribBoundary id="workspace">
        <Boom />
      </ContribBoundary>
    )

    expect(screen.getByText('“workspace” failed to render')).toBeTruthy()
    expect(screen.getByText('Retry')).toBeTruthy()
    expect(screen.getByText('Reset layout & reload')).toBeTruthy()
    expect(screen.getByText(/flips=77 getSnapshot=\(\) => store\.get\(\)/)).toBeTruthy()
  })

  it('leaves non-loop errors on the plain manual fallback', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {})

    function Plain(): null {
      throw new Error('boom')
    }

    render(
      <ContribBoundary id="workspace">
        <Plain />
      </ContribBoundary>
    )

    expect(screen.getByText('“workspace” failed to render')).toBeTruthy()
    expect(screen.queryByText('Reset layout & reload')).toBeNull()
    expect(sessionStorage.getItem('hermes.desktop.workspaceLoopRecovery.v1')).toBeNull()
  })
})
