import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { AssistantMessage } from '@/components/assistant-ui/thread/assistant-message'
import { ThreadMessageList } from '@/components/assistant-ui/thread/list'
import { BackgroundResumeNotice, CenteredThreadSpinner } from '@/components/assistant-ui/thread/status'
import { SystemMessage } from '@/components/assistant-ui/thread/system-message'
import { ThreadTimeline } from '@/components/assistant-ui/thread/timeline'
import { type RestoreMessageTarget } from '@/components/assistant-ui/thread/types'
import { UserEditComposer } from '@/components/assistant-ui/thread/user-edit-composer'
import { UserMessage } from '@/components/assistant-ui/thread/user-message'
import { Intro, type IntroProps } from '@/components/chat/intro'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import type { HermesGateway } from '@/hermes'
import { useI18n } from '@/i18n'
import { notifyError } from '@/store/notifications'
import { resetThreadScroll } from '@/store/thread-scroll'

type ThreadLoadingState = 'response' | 'session'

interface ThreadProps {
  clampToComposer?: boolean
  cwd?: string | null
  gateway?: HermesGateway | null
  intro?: IntroProps
  loading?: ThreadLoadingState
  onBranchInNewChat?: (messageId: string) => void
  onCancel?: () => Promise<void> | void
  onDismissError?: (messageId: string) => void
  onRestoreToMessage?: (messageId: string, target?: RestoreMessageTarget) => Promise<void> | void
  sessionId?: string | null
  sessionKey?: string | null
}

// memo'd on purpose, and load-bearing for session-switch cost. ChatView
// re-renders on every route change (it reads `location`), and this subtree is
// the entire transcript — without a bail-out here the router's context update
// rebuilds every message of the OUTGOING thread before it is replaced. The
// props above are all stable across a plain re-render (see the component-map
// and loadingIndicator memos below), so the only thing that gets through is a
// genuine change.
export const Thread = memo(function Thread({
  clampToComposer = false,
  cwd = null,
  gateway = null,
  intro,
  loading,
  onBranchInNewChat,
  onCancel,
  onDismissError,
  onRestoreToMessage,
  sessionId = null,
  sessionKey
}: ThreadProps) {
  const { t } = useI18n()
  const copy = t.assistant.thread

  const [restoreConfirmTarget, setRestoreConfirmTarget] = useState<
    (RestoreMessageTarget & { messageId: string }) | null
  >(null)

  const closeRestoreConfirm = useCallback(() => setRestoreConfirmTarget(null), [])

  const confirmRestore = useCallback(() => {
    if (!restoreConfirmTarget || !onRestoreToMessage) {
      throw new Error('Restore is unavailable for this message.')
    }

    const { messageId, text, userOrdinal } = restoreConfirmTarget

    closeRestoreConfirm()
    void Promise.resolve(onRestoreToMessage(messageId, { text, userOrdinal })).catch((error: unknown) => {
      notifyError(error, 'Restore failed')
    })
  }, [closeRestoreConfirm, onRestoreToMessage, restoreConfirmTarget])

  const requestRestoreConfirm = useCallback((messageId: string, target: RestoreMessageTarget) => {
    setRestoreConfirmTarget({ messageId, ...target })
  }, [])

  // The values in this map are component *types*: when their identity
  // changes, every row that uses them remounts. Keep it stable.
  const callbacksRef = useRef({ onBranchInNewChat, onCancel, onDismissError })
  callbacksRef.current = { onBranchInNewChat, onCancel, onDismissError }

  const editContextRef = useRef({ cwd, gateway, sessionId })
  editContextRef.current = { cwd, gateway, sessionId }

  const hasBranchInNewChat = Boolean(onBranchInNewChat)
  const hasCancel = Boolean(onCancel)
  const hasDismissError = Boolean(onDismissError)
  const hasRestoreToMessage = Boolean(onRestoreToMessage)

  const messageComponents = useMemo(
    () => ({
      AssistantMessage: () => (
        <AssistantMessage
          onBranchInNewChat={
            hasBranchInNewChat ? messageId => callbacksRef.current.onBranchInNewChat?.(messageId) : undefined
          }
          onDismissError={hasDismissError ? messageId => callbacksRef.current.onDismissError?.(messageId) : undefined}
        />
      ),
      SystemMessage,
      UserEditComposer: () => {
        const { cwd: editCwd, gateway: editGateway, sessionId: editSessionId } = editContextRef.current

        return <UserEditComposer cwd={editCwd} gateway={editGateway} sessionId={editSessionId} />
      },
      UserMessage: () => (
        <UserMessage
          onCancel={hasCancel ? () => callbacksRef.current.onCancel?.() : undefined}
          onRequestRestoreConfirm={hasRestoreToMessage ? requestRestoreConfirm : undefined}
        />
      )
    }),
    [hasBranchInNewChat, hasCancel, hasDismissError, hasRestoreToMessage, requestRestoreConfirm]
  )

  const loadingIndicator = useMemo(() => <BackgroundResumeNotice />, [])

  // New-chat Iris AI intro MUST NOT mount inside ThreadMessageList: that list
  // owns useStickToBottom + setThreadAtBottom → composer ResizeObserver, and a
  // tall intro inside the empty viewport creates a Maximum update depth loop
  // that crashes the whole `workspace` pane (ContribBoundary).
  const showIntro = Boolean(intro)

  useEffect(() => {
    if (showIntro) {
      resetThreadScroll()
    }
  }, [showIntro])

  const restoreDialog = (
    <ConfirmDialog
      confirmLabel={copy.restoreConfirm}
      description={copy.restoreBody}
      destructive
      onClose={closeRestoreConfirm}
      onConfirm={confirmRestore}
      open={Boolean(restoreConfirmTarget)}
      title={copy.restoreTitle}
    />
  )

  if (showIntro) {
    return (
      <div className="relative grid h-full min-h-0 max-w-full grid-rows-[minmax(0,1fr)] overflow-hidden bg-transparent">
        <div
          className="min-h-0 size-full overflow-x-hidden overflow-y-auto overscroll-contain px-3 py-4 sm:px-5"
          data-slot="aui_thread-viewport"
        >
          <Intro personality={intro?.personality} seed={intro?.seed} />
        </div>
        {loading === 'session' && <CenteredThreadSpinner />}
        {restoreDialog}
      </div>
    )
  }

  return (
    <div className="relative grid h-full min-h-0 max-w-full grid-rows-[minmax(0,1fr)] overflow-hidden bg-transparent contain-[layout_paint]">
      <ThreadMessageList
        clampToComposer={clampToComposer}
        components={messageComponents}
        loadingIndicator={loadingIndicator}
        sessionKey={sessionKey}
      />
      {loading === 'session' && <CenteredThreadSpinner />}
      <ThreadTimeline />
      {restoreDialog}
    </div>
  )
})
