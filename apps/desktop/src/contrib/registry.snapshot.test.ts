import { afterEach, describe, expect, it } from 'vitest'

import { registry } from './registry'

const disposers: Array<() => void> = []

afterEach(() => {
  while (disposers.length) {
    disposers.pop()?.()
  }
})

describe('contribution registry snapshots', () => {
  it('returns the same empty array reference until the area mutates', () => {
    const area = `test.snapshot.empty.${Math.random()}`
    const first = registry.getArea(area)
    const second = registry.getArea(area)

    expect(first).toEqual([])
    expect(first).toBe(second)
  })

  it('returns the same populated snapshot until register/remove', () => {
    const area = `test.snapshot.populated.${Math.random()}`
    const dispose = registry.register({ area, id: 'item', title: 'Item' })
    disposers.push(dispose)

    const first = registry.getArea(area)
    const second = registry.getArea(area)

    expect(first).toHaveLength(1)
    expect(first).toBe(second)

    dispose()
    const after = registry.getArea(area)

    expect(after).toEqual([])
    expect(after).not.toBe(first)
  })
})
