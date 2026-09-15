import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ContribBoundary } from './boundary'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function Boom() {
  throw new Error(
    'Maximum update depth exceeded. The result of getSnapshot should be cached to avoid an infinite loop.'
  )
}

describe('ContribBoundary getSnapshot loop', () => {
  it('logs the workspace-loop tag and shows the pane fallback', () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => {})

    sessionStorage.setItem(
      'hermesUsesLoop',
      JSON.stringify({ getSnapshot: 'fake-selector', flips: 51 })
    )

    render(
      <ContribBoundary id="workspace">
        <Boom />
      </ContribBoundary>
    )

    expect(screen.getByText('“workspace” failed to render')).toBeTruthy()
    expect(
      error.mock.calls.some(args => String(args[0]).includes('[workspace-loop]'))
    ).toBe(true)
    expect(
      error.mock.calls.some(
        args => args[2] && typeof args[2] === 'object' && (args[2] as { snapshotLoop?: boolean }).snapshotLoop
      )
    ).toBe(true)

    sessionStorage.removeItem('hermesUsesLoop')
  })
})
