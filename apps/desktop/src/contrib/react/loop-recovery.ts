import { writeKey } from '@/lib/storage'

/**
 * Self-heal for the "Maximum update depth / getSnapshot should be cached"
 * crash that ContribBoundary catches on the `workspace` pane.
 *
 * The loop is state-dependent (hydrating a stored session + persisted layout
 * on a zero-height viewport), so a user who hit it once keeps hitting it on
 * every reload — the Retry button remounts into the same state. Escalate
 * instead of waiting for a click:
 *
 *   1. `reset`   — remount the pane once the current tick has settled.
 *   2. `reload`  — drop the persisted layout / last-session keys that feed
 *                  the loop and reload the document.
 *   3. `manual`  — two escalations inside the window did not help; show the
 *                  fallback with the explicit buttons (never reload-loop).
 *
 * Attempts are tracked per tab in sessionStorage (survives the reload in
 * step 2, dies with the tab) and expire after RECOVERY_WINDOW_MS so a later,
 * unrelated crash gets a fresh ladder.
 */

export type LoopRecoveryPlan = 'manual' | 'reload' | 'reset'

const ATTEMPTS_KEY = 'hermes.desktop.workspaceLoopRecovery.v1'
const RECOVERY_WINDOW_MS = 5 * 60 * 1000

/** Persisted UI state that shapes the workspace pane on boot. Preferences
 *  (model, keybinds, pins, themes) are NOT here — only layout + routing. */
export const LOOP_STATE_KEYS = [
  'hermes.desktop.lastSessionId',
  'hermes.desktop.lastRoute',
  'hermes.desktop.layoutTree.v2',
  'hermes.desktop.layoutPreset.active',
  'hermes.desktop.paneStates.v1',
  'hermes.desktop.floatingPanes.v1',
  'hermes.desktop.sessionTiles.v1',
  'hermes.desktop.sessionTiles.v2',
  'hermes.desktop.routeTiles.v1',
  'hermes.desktop.previewTabs.v2',
  'hermes.desktop.composerPopout.enabled',
  'hermes.desktop.composerPopout.position',
  'hermes.desktop.composerPopout.zones.v1',
  'hermes.desktop.composerQueue.v1',
  'hermes.desktop.inflightTurnJournal.v1',
  'hermes.desktop.sessionPreviews.v1'
] as const

interface Attempts {
  at: number
  n: number
}

export function isSnapshotLoopError(error: { message?: string } | null | undefined): boolean {
  return /Maximum update depth|getSnapshot should be cached/i.test(error?.message ?? '')
}

function readAttempts(now: number): Attempts {
  try {
    const raw = sessionStorage.getItem(ATTEMPTS_KEY)
    const parsed = raw ? (JSON.parse(raw) as Partial<Attempts>) : null

    if (parsed && typeof parsed.n === 'number' && typeof parsed.at === 'number' && now - parsed.at < RECOVERY_WINDOW_MS) {
      return { at: parsed.at, n: parsed.n }
    }
  } catch {
    /* absent / malformed / storage off → fresh ladder */
  }

  return { at: now, n: 0 }
}

function writeAttempts(attempts: Attempts) {
  try {
    sessionStorage.setItem(ATTEMPTS_KEY, JSON.stringify(attempts))
  } catch {
    /* best-effort; without storage every crash is step 1 */
  }
}

/** Decide the next rung of the ladder and record that it was taken. */
export function planSnapshotLoopRecovery(now = Date.now()): LoopRecoveryPlan {
  const attempts = readAttempts(now)
  const plan: LoopRecoveryPlan = attempts.n === 0 ? 'reset' : attempts.n === 1 ? 'reload' : 'manual'

  writeAttempts({ at: now, n: attempts.n + 1 })

  return plan
}

/** Forget the ladder — called once the pane renders cleanly again. */
export function clearSnapshotLoopRecovery() {
  try {
    sessionStorage.removeItem(ATTEMPTS_KEY)
  } catch {
    /* ignore */
  }
}

/** Drop the persisted workspace state that re-creates the loop on boot. */
export function clearLoopState() {
  for (const key of LOOP_STATE_KEYS) {
    writeKey(key, null)
  }
}

/** Step 2/manual button: wipe layout state, then reload the document. */
export function resetWorkspaceAndReload() {
  clearLoopState()
  window.location.reload()
}

/** The tripwire's `[uses-loop]` report (web build only), for the fallback copy. */
export function readUsesLoopReport(): null | { getSnapshot?: string; flips?: number; preview?: string } {
  try {
    const raw = sessionStorage.getItem('hermesUsesLoop')

    return raw ? (JSON.parse(raw) as { getSnapshot?: string; flips?: number; preview?: string }) : null
  } catch {
    return null
  }
}
