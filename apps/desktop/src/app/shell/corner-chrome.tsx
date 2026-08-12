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

const KANBAN_ROUTE = '/kanban'
const PLUGINS_SETTINGS_ROUTE = `${SETTINGS_ROUTE}?tab=plugins`

/** Widest dock row: four size-9 FABs + three gaps. */
const CORNER_CHROME_WIDTH = '10.5rem'

/**
 * Bottom-right chrome dock — FABs moved off the titlebar / Hermes One sidebar:
 *   nav row:   Обзор · Kanban · Расписания · Plugins
 *   chrome row: layout · haptics · settings · keybinds
 * Keybinds expands into a slide-out panel above the rows.
 *
 * Sets `--corner-chrome-width` on `:root` so sibling corner FABs (plugin AI test)
 * can sit to the left without overlapping.
 */
export function CornerChrome() {
  const { t } = useI18n()
  const navigate = useNavigate()
  const location = useLocation()
  const path = routePathname(location.pathname)
  const open = useStore($keybindsPanelOpen)
  const statusbarVisible = useStore($statusbarVisible)
  const capturing = useStore($capture)
  const hapticsMuted = useStore($hapticsMuted)
  const layoutEditing = useStore($layoutEditMode)
  const modHeld = useModifierHeld()
  const embed = typeof window !== 'undefined' && window.__HERMES_DESKTOP_EMBED__ === true
  const nav = t.sidebar.hermesOneNav
  const pluginsLabel = t.settings.nav.plugins

  useEffect(() => {
    if (!open) {
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

      closeKeybindsPanel()
      event.preventDefault()
      event.stopPropagation()
    }

    window.addEventListener('keydown', onKeyDown, true)

    return () => window.removeEventListener('keydown', onKeyDown, true)
  }, [capturing, open])

  useEffect(() => {
    const root = document.documentElement
    root.style.setProperty('--corner-chrome-width', CORNER_CHROME_WIDTH)

    return () => {
      root.style.removeProperty('--corner-chrome-width')
    }
  }, [])

  // Full-screen overlays own the window — same rule as TitlebarControls.
  if (!embed && isOverlayView(appViewForPath(location.pathname))) {
    return null
  }

  const onLayoutClick = (event: MouseEvent) => {
    if (event.metaKey || event.ctrlKey) {
      triggerHaptic('warning')
      resetLayoutTree()

      return
    }

    triggerHaptic('open')
    toggleLayoutEditMode()
  }

  const toggleHaptics = () => {
    if (!hapticsMuted) {
      triggerHaptic('tap')
    }

    toggleHapticsMuted()

    if (hapticsMuted) {
      window.requestAnimationFrame(() => triggerHaptic('success'))
    }
  }

  const go = (to: string, workspace = false) => {
    triggerHaptic('open')

    if (workspace) {
      navigateToWorkspacePage(navigate, to)
    } else {
      navigate(to)
    }
  }

  return createPortal(
    <div
      className={cn(
        'pointer-events-none fixed z-(--z-over-modal) right-3 flex flex-col items-end gap-2 [-webkit-app-region:no-drag]',
        statusbarVisible ? 'bottom-8' : 'bottom-3'
      )}
      data-slot="corner-chrome"
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

      {/* Product nav — was Hermes One sidebar primary (minus New Chat / Office). */}
      <div className="pointer-events-none flex flex-row-reverse items-center gap-2">
        <Tip label={pluginsLabel}>
          <Button
            aria-label={pluginsLabel}
            className={FAB_CLASS}
            onClick={() => go(PLUGINS_SETTINGS_ROUTE)}
            size="icon"
            type="button"
            variant="ghost"
          >
            <Codicon name="extensions" />
          </Button>
        </Tip>

        <Tip label={<TipKeybindLabel actionId="nav.cron" text={nav.schedules} />}>
          <Button
            aria-label={nav.schedules}
            className={cn(FAB_CLASS, path === CRON_ROUTE && 'bg-(--chrome-action-hover)')}
            onClick={() => go(CRON_ROUTE)}
            size="icon"
            type="button"
            variant="ghost"
          >
            <Codicon name="clock" />
          </Button>
        </Tip>

        <Tip label={<TipKeybindLabel actionId="nav.artifacts" text={nav.kanban} />}>
          <Button
            aria-label={nav.kanban}
            className={cn(FAB_CLASS, path === KANBAN_ROUTE && 'bg-(--chrome-action-hover)')}
            onClick={() => go(KANBAN_ROUTE, true)}
            size="icon"
            type="button"
            variant="ghost"
          >
            <Codicon name="project" />
          </Button>
        </Tip>

        <Tip label={<TipKeybindLabel actionId="nav.skills" text={nav.discover} />}>
          <Button
            aria-label={nav.discover}
            className={cn(FAB_CLASS, path === SKILLS_ROUTE && 'bg-(--chrome-action-hover)')}
            onClick={() => go(SKILLS_ROUTE, true)}
            size="icon"
            type="button"
            variant="ghost"
          >
            <Codicon name="symbol-misc" />
          </Button>
        </Tip>
      </div>

      <div className="pointer-events-none flex flex-row-reverse items-center gap-2">
        <Tip label={<TipKeybindLabel actionId="keybinds.openPanel" text={t.titlebar.openKeybinds} />}>
          <Button
            aria-expanded={open}
            aria-label={t.titlebar.openKeybinds}
            className={cn(FAB_CLASS, open && 'bg-(--chrome-action-hover)')}
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

        <Tip label={<TipKeybindLabel actionId="nav.settings" text={t.titlebar.openSettings} />}>
          <Button
            aria-label={t.titlebar.openSettings}
            className={FAB_CLASS}
            onClick={() => go(SETTINGS_ROUTE)}
            size="icon"
            type="button"
            variant="ghost"
          >
            <Codicon name="settings-gear" />
          </Button>
        </Tip>

        <Tip label={hapticsMuted ? t.titlebar.unmuteHaptics : t.titlebar.muteHaptics}>
          <Button
            aria-label={hapticsMuted ? t.titlebar.unmuteHaptics : t.titlebar.muteHaptics}
            aria-pressed={hapticsMuted}
            className={cn(FAB_CLASS, hapticsMuted && 'bg-(--chrome-action-hover)')}
            onClick={toggleHaptics}
            size="icon"
            type="button"
            variant="ghost"
          >
            <Codicon name={hapticsMuted ? 'mute' : 'unmute'} />
          </Button>
        </Tip>

        <Tip label={t.titlebar.layoutEditorTitle}>
          <Button
            aria-label={t.titlebar.layoutEditor}
            aria-pressed={layoutEditing}
            className={cn('group/tool', FAB_CLASS, layoutEditing && 'bg-(--chrome-action-hover)')}
            onClick={onLayoutClick}
            size="icon"
            type="button"
            variant="ghost"
          >
            <LayoutGlyph modHeld={modHeld} />
          </Button>
        </Tip>
      </div>
    </div>,
    document.body
  )
}

/**
 * Layout glyph morphs into reset (layout + refresh badge) only while the
 * pointer is on the button AND ⌘/Ctrl is held — hover gates via CSS, modifier
 * via the window listener.
 */
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
