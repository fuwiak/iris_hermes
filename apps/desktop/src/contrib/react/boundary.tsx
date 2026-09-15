import { type ReactNode, useEffect } from 'react'

import { ErrorBoundary } from '@/components/error-boundary'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { ErrorState } from '@/components/ui/error-state'
import { Tip } from '@/components/ui/tooltip'

import {
  clearSnapshotLoopRecovery,
  isSnapshotLoopError,
  type LoopRecoveryPlan,
  MAX_AUTO_RESETS,
  planSnapshotLoopRecovery,
  readUsesLoopReport,
  resetWorkspaceAndReload
} from './loop-recovery'

interface ContribBoundaryProps {
  children: ReactNode
  /** Contribution key, shown in the fallback + console tag. */
  id: string
  /** `chip` = inline bar item (tiny fallback); `pane` = zone body. */
  variant?: 'chip' | 'pane'
}

/**
 * The blast wall between a contribution's `render()` and the app. Plugin
 * code throwing during render (bad import, undefined component, logic bug)
 * degrades to a small inline error in ITS slot — the surrounding bar/zone,
 * other plugins, and the app keep working. Every surface that mounts
 * contribution renders wraps them in this.
 *
 * The pane fallback uses the app's canonical `ErrorState` (same icon/title/body
 * as the React boundary and dialog errors) so a crashed contribution reads like
 * every other failure, not a raw stack dump.
 */
export function ContribBoundary({ children, id, variant = 'pane' }: ContribBoundaryProps) {
  return (
    <ErrorBoundary
      fallback={({ error, reset }) =>
        variant === 'chip' ? (
          <Tip label={`${id}: ${error.message}`}>
            <button
              className="inline-flex items-center gap-1 rounded px-1.5 text-[0.6875rem] text-destructive transition-colors hover:bg-(--chrome-action-hover)"
              onClick={reset}
              type="button"
            >
              <Codicon name="warning" size="0.7rem" />
              {id}
            </button>
          </Tip>
        ) : (
          <PaneFallback error={error} id={id} reset={reset} />
        )
      }
      label={`contrib:${id}`}
      onError={error => {
        if (!isSnapshotLoopError(error)) {
          return
        }

         
        console.error(`[workspace-loop] contrib:${id} crashed on getSnapshot loop`, {
          id,
          message: error.message,
          stack: error.stack?.slice(0, 4000)
        })
      }}
    >
      {children}
      {variant === 'pane' ? <RecoveryHealthProbe /> : null}
    </ErrorBoundary>
  )
}

const HEALTHY_AFTER_MS = 15_000

/** Mounted next to the pane's children: once they stay up this long, the
 *  recovery ladder is forgotten so the NEXT crash starts from step 1 again. */
function RecoveryHealthProbe() {
  useEffect(() => {
    const timer = window.setTimeout(clearSnapshotLoopRecovery, HEALTHY_AFTER_MS)

    return () => window.clearTimeout(timer)
  }, [])

  return null
}

const AUTO_RESET_DELAY_MS = 400

// React may render the fallback more than once for ONE crash (concurrent
// render recovery re-throws a NEW Error, StrictMode double-mounts). The ladder
// must climb once per incident, not once per mount — reuse the plan decided
// within the last second.
const SAME_INCIDENT_MS = 1000
let lastPlan: { at: number; plan: LoopRecoveryPlan } | null = null

function planFor(error: Error): LoopRecoveryPlan {
  if (!isSnapshotLoopError(error)) {
    return 'manual'
  }

  const now = Date.now()

  if (lastPlan && now - lastPlan.at < SAME_INCIDENT_MS) {
    return lastPlan.plan
  }

  lastPlan = { at: now, plan: planSnapshotLoopRecovery(now) }

  return lastPlan.plan
}

interface PaneFallbackProps {
  error: Error
  id: string
  reset: () => void
}

/**
 * Pane fallback. For the getSnapshot loop it does not wait for a click: the
 * pane is remounted (chat state lives in stores, nothing is lost) up to
 * MAX_AUTO_RESETS times a minute. It never reloads the document by itself —
 * that would drop the WebSocket and the in-flight turn. Past the budget it
 * shows the buttons + the tripwire's diagnostics; see loop-recovery.ts.
 */
function PaneFallback({ error, id, reset }: PaneFallbackProps) {
  const loop = isSnapshotLoopError(error)
  const plan = planFor(error)

  useEffect(() => {
    if (plan !== 'reset') {
      return undefined
    }

    const timer = window.setTimeout(reset, AUTO_RESET_DELAY_MS)

    return () => window.clearTimeout(timer)
  }, [plan, reset])

  if (plan === 'reset') {
    // Blank for a few hundred ms instead of flashing the error card.
    return <div className="h-full" data-loop-recovery="reset" />
  }

  const report = loop ? readUsesLoopReport() : null

  const copyDiagnostics = () => {
    const text = JSON.stringify(
      { id, message: error.message, stack: error.stack?.slice(0, 4000), usesLoop: report },
      null,
      2
    )

    void navigator.clipboard?.writeText(text).catch(() => undefined)
  }

  return (
    <div className="grid h-full place-items-center p-6">
      <ErrorState
        description={
          loop ? (
            <>
              {error.message}
              <br />
              <span className="mt-1 block text-[0.75rem] opacity-80">
                {`Remounted ${MAX_AUTO_RESETS}× in the last minute without success.`}
              </span>
              {report?.getSnapshot ? (
                <code className="mt-2 block max-w-prose whitespace-pre-wrap break-all text-left text-[0.6875rem] opacity-70">
                  {`flips=${report.flips ?? '?'} getSnapshot=${report.getSnapshot.slice(0, 200)}`}
                </code>
              ) : null}
            </>
          ) : (
            error.message
          )
        }
        title={`“${id}” failed to render`}
      >
        <Button className="justify-self-center" onClick={reset} size="sm" variant="outline">
          <Codicon name="refresh" size="0.8rem" />
          Retry
        </Button>
        {loop ? (
          <>
            <Button className="justify-self-center" onClick={copyDiagnostics} size="sm" variant="text">
              <Codicon name="copy" size="0.8rem" />
              Copy diagnostics
            </Button>
            <Button className="justify-self-center" onClick={resetWorkspaceAndReload} size="sm" variant="text">
              Reset layout & reload
            </Button>
          </>
        ) : null}
      </ErrorState>
    </div>
  )
}
