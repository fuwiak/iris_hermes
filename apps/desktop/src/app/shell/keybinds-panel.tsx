import { useStore } from '@nanostores/react'
import { useEffect } from 'react'
import { createPortal } from 'react-dom'

import { KeybindSettings } from '@/app/settings/keybind-settings'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { Tip, TipKeybindLabel } from '@/components/ui/tooltip'
import { useI18n } from '@/i18n'
import { triggerHaptic } from '@/lib/haptics'
import { cn } from '@/lib/utils'
import {
  $capture,
  $keybindsPanelOpen,
  closeKeybindsPanel,
  endCapture,
  toggleKeybindsPanel
} from '@/store/keybinds'
import { $statusbarVisible } from '@/store/statusbar-prefs'

/**
 * Bottom-right collapsible keybinds editor. Replaces the old titlebar keyboard
 * tool: collapsed = icon button; expanded = floating panel that slides up.
 * `keybinds.openPanel` (mod+/) and the palette entry toggle the same atom.
 */
export function KeybindsPanel() {
  const { t } = useI18n()
  const open = useStore($keybindsPanelOpen)
  const statusbarVisible = useStore($statusbarVisible)
  const capturing = useStore($capture)

  useEffect(() => {
    if (!open) {
      return
    }

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') {
        return
      }

      // Capture-phase rebind eats Esc first via the global keybind dispatcher;
      // if a row is still armed, disarm it before closing the panel.
      if (capturing) {
        endCapture()
        event.preventDefault()
        event.stopPropagation()

        return
      }

      closeKeybindsPanel()
      event.preventDefault()
      event.stopPropagation()
    }

    window.addEventListener('keydown', onKeyDown, true)

    return () => window.removeEventListener('keydown', onKeyDown, true)
  }, [capturing, open])

  return createPortal(
    <div
      className={cn(
        'pointer-events-none fixed z-(--z-over-modal) right-3 flex flex-col items-end gap-2 [-webkit-app-region:no-drag]',
        statusbarVisible ? 'bottom-8' : 'bottom-3'
      )}
      data-slot="keybinds-panel"
    >
      {open && (
        <div
          aria-label={t.keybinds.title}
          className="pointer-events-auto flex w-[min(28rem,calc(100vw-1.5rem))] max-h-[min(70vh,36rem)] flex-col overflow-hidden rounded-xl border border-(--stroke-nous) bg-(--ui-chat-bubble-background) shadow-nous duration-150 animate-in fade-in-0 slide-in-from-bottom-2"
          role="dialog"
        >
          <div className="flex shrink-0 items-center justify-between gap-2 border-b border-(--ui-stroke-secondary)/60 px-3 py-2">
            <div className="flex min-w-0 items-center gap-2">
              <Codicon className="text-muted-foreground" name="keyboard" size="0.875rem" />
              <h2 className="truncate text-sm font-semibold text-foreground">{t.keybinds.title}</h2>
            </div>
            <Button
              aria-label={t.common.close}
              onClick={() => {
                triggerHaptic('tap')
                closeKeybindsPanel()
              }}
              size="icon-sm"
              type="button"
              variant="ghost"
            >
              <Codicon name="close" size="0.875rem" />
            </Button>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto scrollbar-dt">
            <KeybindSettings embedded />
          </div>
        </div>
      )}

      <Tip label={<TipKeybindLabel actionId="keybinds.openPanel" text={t.titlebar.openKeybinds} />}>
        <Button
          aria-expanded={open}
          aria-label={t.titlebar.openKeybinds}
          className={cn(
            'pointer-events-auto size-9 rounded-full border border-(--stroke-nous) bg-(--ui-chat-bubble-background) shadow-nous',
            open && 'bg-(--chrome-action-hover)'
          )}
          onClick={() => {
            triggerHaptic(open ? 'tap' : 'open')
            toggleKeybindsPanel()
          }}
          size="icon"
          type="button"
          variant="ghost"
        >
          <Codicon name="keyboard" />
        </Button>
      </Tip>
    </div>,
    document.body
  )
}
