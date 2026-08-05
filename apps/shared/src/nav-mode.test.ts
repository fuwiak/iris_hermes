import { describe, expect, it } from 'vitest'

import {
  isStandardDesktopPrimaryNavId,
  parseNavMode,
  STANDARD_NAV_PLUGIN_PATHS,
  STANDARD_WEB_CORE_PATHS
} from './nav-mode'

describe('nav-mode', () => {
  it('parseNavMode only accepts pro; everything else is standard', () => {
    expect(parseNavMode('pro')).toBe('pro')
    expect(parseNavMode('standard')).toBe('standard')
    expect(parseNavMode(null)).toBe('standard')
    expect(parseNavMode('nope')).toBe('standard')
  })

  it('isStandardDesktopPrimaryNavId keeps Chat + Settings (incl. embed prefix)', () => {
    expect(isStandardDesktopPrimaryNavId('new-session')).toBe(true)
    expect(isStandardDesktopPrimaryNavId('settings')).toBe(true)
    expect(isStandardDesktopPrimaryNavId('app-control-settings')).toBe(true)
    expect(isStandardDesktopPrimaryNavId('skills')).toBe(false)
    expect(isStandardDesktopPrimaryNavId('app-control-layout')).toBe(false)
    expect(isStandardDesktopPrimaryNavId('app-control-keybinds')).toBe(false)
  })

  it('standard allowlists cover Chat/Settings and MoySklad paths', () => {
    expect(STANDARD_WEB_CORE_PATHS.has('/chat')).toBe(true)
    expect(STANDARD_WEB_CORE_PATHS.has('/settings')).toBe(true)
    expect(STANDARD_NAV_PLUGIN_PATHS.has('/clients')).toBe(true)
    expect(STANDARD_NAV_PLUGIN_PATHS.has('/campaigns')).toBe(true)
  })
})
