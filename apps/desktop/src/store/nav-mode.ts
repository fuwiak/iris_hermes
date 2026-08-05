import { atom } from 'nanostores'

import {
  NAV_MODE_CHANGE_EVENT,
  NAV_MODE_STORAGE_KEY,
  type NavMode,
  parseNavMode,
  readNavMode,
  writeNavMode
} from '@hermes/shared'

/** Left-nav density — shared key with web dashboard (`hermes-nav-mode`). */
export const $navMode = atom<NavMode>(readNavMode())

export function setNavMode(mode: NavMode) {
  writeNavMode(mode)
  $navMode.set(mode)
}

function syncFromStorage() {
  $navMode.set(readNavMode())
}

if (typeof window !== 'undefined') {
  window.addEventListener('storage', e => {
    if (e.key !== null && e.key !== NAV_MODE_STORAGE_KEY) return
    syncFromStorage()
  })
  window.addEventListener(NAV_MODE_CHANGE_EVENT, e => {
    const detail = (e as CustomEvent<NavMode>).detail
    $navMode.set(parseNavMode(detail))
  })
}
