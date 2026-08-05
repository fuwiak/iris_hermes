import { describe, expect, it } from 'vitest'

import {
  isStandardDesktopPrimaryNavId,
  isStandardNavPluginContribution,
  isStandardNavPluginPath,
  navPathname,
  parseNavMode,
  STANDARD_MOYSKLAD_NAV_ITEMS,
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
    expect(STANDARD_MOYSKLAD_NAV_ITEMS.map(i => i.path).sort()).toEqual([
      '/campaigns',
      '/clients'
    ])
  })

  it('isStandardNavPluginPath strips query/hash and keeps MoySklad routes', () => {
    expect(navPathname('/clients?x=1')).toBe('/clients')
    expect(navPathname('/campaigns#top')).toBe('/campaigns')
    expect(isStandardNavPluginPath('/clients')).toBe(true)
    expect(isStandardNavPluginPath('/campaigns?tab=auto')).toBe(true)
    expect(isStandardNavPluginPath('/settings?tab=plugins')).toBe(false)
    expect(isStandardNavPluginPath('/kanban')).toBe(false)
  })

  it('isStandardNavPluginContribution keeps MoySklad paths, not Plugins shortcut', () => {
    expect(
      isStandardNavPluginContribution({
        id: 'moysklad:clients-nav',
        path: '/clients',
        source: 'plugin:moysklad'
      })
    ).toBe(true)
    expect(
      isStandardNavPluginContribution({
        id: 'moysklad:campaigns-nav',
        path: '/campaigns',
        source: 'plugin:moysklad'
      })
    ).toBe(true)
    expect(
      isStandardNavPluginContribution({
        id: 'moysklad:plugins-nav',
        path: '/settings?tab=plugins',
        source: 'plugin:moysklad'
      })
    ).toBe(false)
    expect(isStandardNavPluginContribution({ path: '/clients' })).toBe(true)
  })
})
