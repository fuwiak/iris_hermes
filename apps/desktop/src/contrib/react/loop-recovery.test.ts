import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import {
  clearLoopState,
  clearSnapshotLoopRecovery,
  isSnapshotLoopError,
  LOOP_STATE_KEYS,
  planSnapshotLoopRecovery
} from './loop-recovery'

beforeEach(() => {
  sessionStorage.clear()
  localStorage.clear()
})

afterEach(() => {
  clearSnapshotLoopRecovery()
})

describe('isSnapshotLoopError', () => {
  it('matches both React 19 wordings only', () => {
    expect(isSnapshotLoopError(new Error('Maximum update depth exceeded.'))).toBe(true)
    expect(
      isSnapshotLoopError(new Error('The result of getSnapshot should be cached to avoid an infinite loop.'))
    ).toBe(true)
    expect(isSnapshotLoopError(new Error('boom'))).toBe(false)
    expect(isSnapshotLoopError(null)).toBe(false)
  })
})

describe('planSnapshotLoopRecovery', () => {
  it('escalates reset → reload → manual within the window', () => {
    const t0 = 1_000_000

    expect(planSnapshotLoopRecovery(t0)).toBe('reset')
    expect(planSnapshotLoopRecovery(t0 + 1_000)).toBe('reload')
    expect(planSnapshotLoopRecovery(t0 + 2_000)).toBe('manual')
    expect(planSnapshotLoopRecovery(t0 + 3_000)).toBe('manual')
  })

  it('starts a fresh ladder after the window expires', () => {
    const t0 = 1_000_000

    expect(planSnapshotLoopRecovery(t0)).toBe('reset')
    expect(planSnapshotLoopRecovery(t0 + 1_000)).toBe('reload')
    expect(planSnapshotLoopRecovery(t0 + 6 * 60 * 1000)).toBe('reset')
  })

  it('starts a fresh ladder once the pane is healthy again', () => {
    expect(planSnapshotLoopRecovery(1)).toBe('reset')
    clearSnapshotLoopRecovery()
    expect(planSnapshotLoopRecovery(2)).toBe('reset')
  })

  it('ignores a malformed attempts record', () => {
    sessionStorage.setItem('hermes.desktop.workspaceLoopRecovery.v1', '{nope')
    expect(planSnapshotLoopRecovery(1)).toBe('reset')
  })
})

describe('clearLoopState', () => {
  it('removes layout/session keys and keeps preferences', () => {
    for (const key of LOOP_STATE_KEYS) {
      localStorage.setItem(key, 'x')
    }

    localStorage.setItem('hermes.desktop.composer.model', 'keep-me')
    localStorage.setItem('hermes.desktop.pinnedSessions', '["a"]')

    clearLoopState()

    for (const key of LOOP_STATE_KEYS) {
      expect(localStorage.getItem(key)).toBeNull()
    }

    expect(localStorage.getItem('hermes.desktop.composer.model')).toBe('keep-me')
    expect(localStorage.getItem('hermes.desktop.pinnedSessions')).toBe('["a"]')
  })
})
