import { useStore } from '@nanostores/react'
import { type MouseEvent, useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { useLocation, useNavigate } from 'react-router'

import { KeybindSettings } from '@/app/settings/keybind-settings'
import { $layoutEditMode, toggleLayoutEditMode } from '@/components/pane-shell/edit-mode'
import { resetLayoutTree } from '@/components/pane-shell/tree/store'
import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { Tip, TipKeybindLabel } from '@/components/ui/tooltip'
import { useI18n } from '@/i18n'
import { triggerHaptic } from '@/lib/haptics'
import { cn } from '@/lib/utils'
import { $hapticsMuted, toggleHapticsMuted } from '@/store/haptics'
import {
  $capture,
  $keybindsPanelOpen,
  closeKeybindsPanel,
  endCapture,
  toggleKeybindsPanel
} from '@/store/keybinds'
import { $statusbarVisible } from '@/store/statusbar-prefs'

import { appViewForPath, CRON_ROUTE, isOverlayView, navigateToWorkspacePage, routePathname, SETTINGS_ROUTE, SKILLS_ROUTE } from '../routes'

const FAB_CLASS =
  'pointer-events-auto size-9 rounded-full border border-(--stroke-nous) bg-(--ui-chat-bubble-background) shadow-nous'

const DOCK_ITEM_CLASS =
  'pointer-events-auto flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm text-foreground transition-colors hover:bg-(--chrome-action-hover) focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-(--ui-stroke-focus)'

const KANBAN_ROUTE = '/kanban'
const PLUGINS_SETTINGS_ROUTE = `${SETTINGS_ROUTE}?tab=plugins`

/** Single dock FAB width — plugin corner panels offset via --corner-chrome-width. */
const CORNER_CHROME_WIDTH = '2.25rem'
/** Horizontal clearance for the dock FAB (right-3 + FAB + gap) — composer dock uses this. */
const CORNER_CHROME_RESERVE = `calc(0.75rem + ${CORNER_CHROME_WIDTH} + 0.5rem)`

/**
 * Bottom-right chrome — one FAB opens a drawer with nav + chrome actions.
 * Keybinds still expands its panel above the dock when chosen from the drawer.
 */
export function CornerChrome() {
  const { t } = useI18n()
  const navigate = useNavigate()
  const location = useLocation()
  const path = routePathname(location.pathname)
  const keybindsOpen = useStore($keybindsPanelOpen)
  const statusbarVisible = useStore($statusbarVisible)
  const capturing = useStore($capture)
  const hapticsMuted = useStore($hapticsMuted)
  const layoutEditing = useStore($layoutEditMode)
  const modHeld = useModifierHeld()
  const [dockOpen, setDockOpen] = useState(false)
  const embed = typeof window !== 'undefined' && window.__HERMES_DESKTOP_EMBED__ === true
  const nav = t.sidebar.hermesOneNav
  const pluginsLabel = t.settings.nav.plugins

  useEffect(() => {
    if (!keybindsOpen && !dockOpen) {
      return
    }

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') {
        return
      }

      if (capturing) {
        endCapture()
        event.preventDefault()
        event.stopPropagation()

        return
      }

      if (keybindsOpen) {
        closeKeybindsPanel()
        event.preventDefault()
        event.stopPropagation()

        return
      }

      if (dockOpen) {
        setDockOpen(false)
        event.preventDefault()
        event.stopPropagation()
      }
    }

    window.addEventListener('keydown', onKeyDown, true)

    return () => window.removeEventListener('keydown', onKeyDown, true)
  }, [capturing, dockOpen, keybindsOpen])

  const hidden = !embed && isOverlayView(appViewForPath(location.pathname))

  useEffect(() => {
    const root = document.documentElement

    if (hidden) {
      root.style.setProperty('--corner-chrome-width', '0px')
      root.style.setProperty('--corner-chrome-reserve', '0px')

      return () => {
        root.style.removeProperty('--corner-chrome-width')
        root.style.removeProperty('--corner-chrome-reserve')
      }
    }

    root.style.setProperty('--corner-chrome-width', CORNER_CHROME_WIDTH)
    root.style.setProperty('--corner-chrome-reserve', CORNER_CHROME_RESERVE)

    return () => {
      root.style.removeProperty('--corner-chrome-width')
      root.style.removeProperty('--corner-chrome-reserve')
    }
  }, [hidden])

  if (hidden) {
    return null
  }

  const closeDock = () => setDockOpen(false)

  const onLayoutClick = (event: MouseEvent) => {
    if (event.metaKey || event.ctrlKey) {
      triggerHaptic('warning')
      resetLayoutTree()
      closeDock()

      return
    }

    triggerHaptic('open')
    toggleLayoutEditMode()
    closeDock()
  }

  const toggleHaptics = () => {
    if (!hapticsMuted) {
      triggerHaptic('tap')
    }

    toggleHapticsMuted()

    if (hapticsMuted) {
      window.requestAnimationFrame(() => triggerHaptic('success'))
    }

    closeDock()
  }

  const go = (to: string, workspace = false) => {
    triggerHaptic('open')
    closeDock()

    if (workspace) {
      navigateToWorkspacePage(navigate, to)
    } else {
      navigate(to)
    }
  }

  const openKeybinds = () => {
    triggerHaptic(keybindsOpen ? 'tap' : 'open')
    toggleKeybindsPanel()
    closeDock()
  }

  return createPortal(
    <div
      className={cn(
        // Sit above the composer dock so «Быстрые действия» never covers «Отправить».
        'pointer-events-none fixed z-(--z-over-modal) right-3 flex flex-col items-end gap-2 [-webkit-app-region:no-drag]',
        statusbarVisible
          ? 'bottom-[calc(var(--composer-measured-height,3.5rem)+2rem)]'
          : 'bottom-[calc(var(--composer-measured-height,3.5rem)+0.5rem)]'
      )}
      data-slot="corner-chrome"
    >
      {keybindsOpen ? (
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
      ) : null}

      {dockOpen ? (
        <nav
          aria-label={t.titlebar.cornerDock}
          className="pointer-events-auto flex w-[min(16rem,calc(100vw-1.5rem))] flex-col gap-0.5 rounded-xl border border-(--stroke-nous) bg-(--ui-chat-bubble-background) p-1.5 shadow-nous duration-150 animate-in fade-in-0 slide-in-from-bottom-2"
        >
          <button
            className={cn(DOCK_ITEM_CLASS, path === SKILLS_ROUTE && 'bg-(--chrome-action-hover)')}
            onClick={() => go(SKILLS_ROUTE, true)}
            type="button"
          >
            <Codicon className="shrink-0 text-muted-foreground" name="symbol-misc" />
            <span className="min-w-0 truncate">{nav.discover}</span>
          </button>
          <button
            className={cn(DOCK_ITEM_CLASS, path === KANBAN_ROUTE && 'bg-(--chrome-action-hover)')}
            onClick={() => go(KANBAN_ROUTE, true)}
            type="button"
          >
            <Codicon className="shrink-0 text-muted-foreground" name="project" />
            <span className="min-w-0 truncate">{nav.kanban}</span>
          </button>
          <button
            className={cn(DOCK_ITEM_CLASS, path === CRON_ROUTE && 'bg-(--chrome-action-hover)')}
            onClick={() => go(CRON_ROUTE)}
            type="button"
          >
            <Codicon className="shrink-0 text-muted-foreground" name="clock" />
            <span className="min-w-0 truncate">{nav.schedules}</span>
          </button>
          <button className={DOCK_ITEM_CLASS} onClick={() => go(PLUGINS_SETTINGS_ROUTE)} type="button">
            <Codicon className="shrink-0 text-muted-foreground" name="extensions" />
            <span className="min-w-0 truncate">{pluginsLabel}</span>
          </button>

          <div aria-hidden className="my-0.5 h-px bg-(--ui-stroke-secondary)/60" />

          <button
            className={cn('group/tool', DOCK_ITEM_CLASS, layoutEditing && 'bg-(--chrome-action-hover)')}
            onClick={onLayoutClick}
            title={t.titlebar.layoutEditorTitle}
            type="button"
          >
            <span className="inline-flex w-4 shrink-0 justify-center">
              <LayoutGlyph modHeld={modHeld} />
            </span>
            <span className="min-w-0 truncate">{t.titlebar.layoutEditor}</span>
          </button>
          <button
            aria-pressed={hapticsMuted}
            className={cn(DOCK_ITEM_CLASS, hapticsMuted && 'bg-(--chrome-action-hover)')}
            onClick={toggleHaptics}
            type="button"
          >
            <Codicon className="shrink-0 text-muted-foreground" name={hapticsMuted ? 'mute' : 'unmute'} />
            <span className="min-w-0 truncate">
              {hapticsMuted ? t.titlebar.unmuteHaptics : t.titlebar.muteHaptics}
            </span>
          </button>
          <button className={DOCK_ITEM_CLASS} onClick={() => go(SETTINGS_ROUTE)} type="button">
            <Codicon className="shrink-0 text-muted-foreground" name="settings-gear" />
            <span className="min-w-0 truncate">{t.titlebar.openSettings}</span>
          </button>
          <button
            aria-expanded={keybindsOpen}
            className={cn(DOCK_ITEM_CLASS, keybindsOpen && 'bg-(--chrome-action-hover)')}
            onClick={openKeybinds}
            type="button"
          >
            <Codicon className="shrink-0 text-muted-foreground" name="keyboard" />
            <span className="min-w-0 truncate">{t.titlebar.openKeybinds}</span>
          </button>
        </nav>
      ) : null}

      <Tip label={t.titlebar.cornerDock}>
        <Button
          aria-expanded={dockOpen}
          aria-label={t.titlebar.cornerDock}
          className={cn(FAB_CLASS, dockOpen && 'bg-(--chrome-action-hover)')}
          onClick={() => {
            triggerHaptic(dockOpen ? 'tap' : 'open')
            setDockOpen(prev => !prev)
          }}
          size="icon"
          type="button"
          variant="ghost"
        >
          <Codicon name={dockOpen ? 'close' : 'kebab-vertical'} />
        </Button>
      </Tip>
    </div>,
    document.body
  )
}

function LayoutGlyph({ modHeld }: { modHeld: boolean }) {
  return (
    <>
      <span className={cn('inline-flex', modHeld && 'group-hover/tool:hidden')}>
        <Codicon name="layout" />
      </span>
      <span className={cn('relative hidden', modHeld && 'group-hover/tool:inline-flex')}>
        <Codicon name="layout" />
        <span className="absolute -bottom-1 -right-1.5 grid place-items-center rounded-full bg-(--ui-bg-chrome) p-px">
          <Codicon className="-scale-x-100" name="refresh" size="0.5625rem" />
        </span>
      </span>
    </>
  )
}

function useModifierHeld(): boolean {
  const [held, setHeld] = useState(false)

  useEffect(() => {
    const sync = (event: KeyboardEvent) => setHeld(event.metaKey || event.ctrlKey)
    const clear = () => setHeld(false)

    window.addEventListener('keydown', sync)
    window.addEventListener('keyup', sync)
    window.addEventListener('blur', clear)

    return () => {
      window.removeEventListener('keydown', sync)
      window.removeEventListener('keyup', sync)
      window.removeEventListener('blur', clear)
    }
  }, [])

  return held
}

/** @deprecated Prefer CornerChrome — kept so older imports keep working. */
export const KeybindsPanel = CornerChrome
