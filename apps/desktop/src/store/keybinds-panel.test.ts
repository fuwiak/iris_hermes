import { afterEach, describe, expect, it } from 'vitest'

import {
  $capture,
  $keybindsPanelOpen,
  beginCapture,
  closeKeybindsPanel,
  openKeybindsPanel,
  toggleKeybindsPanel
} from '@/store/keybinds'

afterEach(() => {
  $keybindsPanelOpen.set(false)
  $capture.set(null)
})

describe('keybinds floating panel store', () => {
  it('opens, closes, and toggles', () => {
    expect($keybindsPanelOpen.get()).toBe(false)
    openKeybindsPanel()
    expect($keybindsPanelOpen.get()).toBe(true)
    toggleKeybindsPanel()
    expect($keybindsPanelOpen.get()).toBe(false)
    toggleKeybindsPanel()
    expect($keybindsPanelOpen.get()).toBe(true)
    closeKeybindsPanel()
    expect($keybindsPanelOpen.get()).toBe(false)
  })

  it('close clears an active rebind capture', () => {
    openKeybindsPanel()
    beginCapture('nav.settings')
    expect($capture.get()).toBe('nav.settings')
    closeKeybindsPanel()
    expect($keybindsPanelOpen.get()).toBe(false)
    expect($capture.get()).toBe(null)
  })
})
