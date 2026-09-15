import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import {
  clearLoopState,
  clearSnapshotLoopRecovery,
  isSnapshotLoopError,
  LOOP_STATE_KEYS,
  MAX_AUTO_RESETS,
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
  it('auto-remounts MAX_AUTO_RESETS times, then goes manual', () => {
    const t0 = 1_000_000

    for (let i = 0; i < MAX_AUTO_RESETS; i += 1) {
      expect(planSnapshotLoopRecovery(t0 + i * 1_000)).toBe('reset')
    }

    expect(planSnapshotLoopRecovery(t0 + 10_000)).toBe('manual')
    expect(planSnapshotLoopRecovery(t0 + 11_000)).toBe('manual')
  })

  it('anchors the window at the first crash so steady crashes cannot slide it', () => {
    const t0 = 1_000_000

    for (let i = 0; i < MAX_AUTO_RESETS; i += 1) {
      expect(planSnapshotLoopRecovery(t0 + i * 20_000)).toBe('reset')
    }

    // t0 + 60s: the window (anchored at t0) is over → fresh budget.
    expect(planSnapshotLoopRecovery(t0 + 60_000)).toBe('reset')
  })

  it('starts a fresh budget after the window expires', () => {
    const t0 = 1_000_000

    for (let i = 0; i <= MAX_AUTO_RESETS; i += 1) {
      planSnapshotLoopRecovery(t0)
    }

    expect(planSnapshotLoopRecovery(t0)).toBe('manual')
    expect(planSnapshotLoopRecovery(t0 + 2 * 60 * 1000)).toBe('reset')
  })

  it('starts a fresh budget once the pane is healthy again', () => {
    for (let i = 0; i <= MAX_AUTO_RESETS; i += 1) {
      planSnapshotLoopRecovery(1)
    }

    expect(planSnapshotLoopRecovery(1)).toBe('manual')
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
