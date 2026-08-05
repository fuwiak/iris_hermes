import { configure } from '@testing-library/react'

import { setRuntimeI18nLocale } from './src/i18n/runtime'

// Belt-and-suspenders with vitest.config `env` — product default is Russian.
process.env.HERMES_UI_TEST_LOCALE = 'en'
setRuntimeI18nLocale('en')

// Node 22+/26 may expose a `localStorage` accessor that is unusable without
// `--localstorage-file` (warns: "localStorage is not available because
// --localstorage-file was not provided"). In jsdom, that accessor shadows
// Storage and `localStorage.getItem(...)` throws. Always install an
// in-memory Storage when the global one is missing or not a real Storage.
;(() => {
  let current: Storage | null | undefined

  try {
    current = (globalThis as { localStorage?: Storage }).localStorage
  } catch {
    current = undefined
  }

  if (current && typeof current.getItem === 'function' && typeof current.setItem === 'function') {
    return
  }

  const store = new Map<string, string>()

  const storage: Storage = {
    get length() {
      return store.size
    },
    key: (i: number) => [...store.keys()][i] ?? null,
    getItem: (k: string) => store.get(String(k)) ?? null,
    setItem: (k: string, v: string) => void store.set(String(k), String(v)),
    removeItem: (k: string) => void store.delete(String(k)),
    clear: () => store.clear(),
  }

  for (const target of [globalThis, (globalThis as { window?: unknown }).window].filter(Boolean)) {
    Object.defineProperty(target, 'localStorage', {
      value: storage,
      configurable: true,
      writable: true,
    })
  }
})()

// React 19 + Testing Library 16: opt into the act environment so render(),
// fireEvent(), and findBy* queries automatically flush state updates without
// spurious "not wrapped in act(...)" warnings.
;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true

// findBy*/waitFor default to a 1000ms deadline — too tight for async-heavy
// panels (radix menus, refetch chains) when the full suite runs under xdist
// CPU contention in CI. Success still resolves the instant the node appears;
// the wider deadline only absorbs a starved runner, killing timing flakes.
configure({ asyncUtilTimeout: 5000 })
