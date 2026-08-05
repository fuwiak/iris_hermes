// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { atom } from 'nanostores'
import { MemoryRouter } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { DISCOVER_SCREEN, DISCOVER_WORKFLOW_CARDS } from '@/app/hermes-one-ui.fixture'
import { en } from '@/i18n/en'
import type { McpCatalogEntry, SkillHubResult } from '@/types/hermes'

const getSkillHubSources = vi.fn()
const searchSkillsHub = vi.fn()
const getMcpCatalog = vi.fn()
const installMcpCatalogEntry = vi.fn()
const openExternalLink = vi.fn()
const installHubSkill = vi.fn()
const uninstallHubSkill = vi.fn()

vi.mock('@/hermes', () => ({
  getMcpCatalog: () => getMcpCatalog(),
  getSkillHubSources: () => getSkillHubSources(),
  installMcpCatalogEntry: (name: string, env: unknown) => installMcpCatalogEntry(name, env),
  searchSkillsHub: (query: string, source: string, limit: number) => searchSkillsHub(query, source, limit)
}))

vi.mock('@/lib/external-link', () => ({
  openExternalLink: (href: string) => openExternalLink(href)
}))

vi.mock('@/store/hub-actions', () => ({
  $hubActions: atom({}),
  $hubInstalledOverride: atom({}),
  HUB_SOURCES_KEY: ['skill-hub-sources'],
  installHubSkill: (id: string) => installHubSkill(id),
  uninstallHubSkill: (id: string, name: string) => uninstallHubSkill(id, name)
}))

vi.mock('@/store/notifications', () => ({
  notify: vi.fn(),
  notifyError: vi.fn()
}))

function skill(patch: Partial<SkillHubResult> = {}): SkillHubResult {
  return {
    description: 'Reproduce a reported bug, fix it, and add a regression test.',
    identifier: 'hermes/bug-fix',
    name: 'Bug Fix',
    repo: null,
    source: 'Hermes Registry',
    tags: ['debugging', 'testing'],
    trust_level: 'builtin',
    ...patch
  }
}

function mcpEntry(patch: Partial<McpCatalogEntry> = {}): McpCatalogEntry {
  return {
    auth_type: 'none',
    description: 'A catalog server.',
    installed: false,
    name: 'filesystem',
    required_env: [],
    source: 'Hermes Catalog',
    transport: 'stdio',
    ...patch
  } as McpCatalogEntry
}

beforeEach(() => {
  installHubSkill.mockResolvedValue(undefined)
  uninstallHubSkill.mockResolvedValue(undefined)
  getSkillHubSources.mockResolvedValue({
    featured: [skill()],
    index_available: true,
    installed: {},
    sources: [{ id: 'hermes', searchable: true }]
  })
  searchSkillsHub.mockResolvedValue({ installed: {}, results: [], source_counts: {}, timed_out: [] })
  getMcpCatalog.mockResolvedValue({ entries: [mcpEntry()] })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

async function renderDiscover() {
  const { DiscoverView } = await import('./discover')
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  let result: ReturnType<typeof render>

  await act(async () => {
    result = render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <DiscoverView />
        </MemoryRouter>
      </QueryClientProvider>
    )
  })

  return result!
}

/** The wide tab row; the narrow dropdown renders the same labels. */
function wideTabRow(container: HTMLElement): HTMLElement {
  const row = container.querySelector('.md\\:flex')

  expect(row).not.toBeNull()

  return row as HTMLElement
}

describe('Discover — English catalog matches the reference screenshot', () => {
  const d = en.skills.discover

  it('keeps the page title and subtitle verbatim', () => {
    expect(d.title).toBe(DISCOVER_SCREEN.title)
    expect(d.subtitle).toBe(DISCOVER_SCREEN.subtitle)
  })

  it('keeps the header actions verbatim', () => {
    expect(d.openRegistry).toBe(DISCOVER_SCREEN.openRegistry)
    expect(d.refresh).toBe(DISCOVER_SCREEN.refresh)
    expect(d.install).toBe(DISCOVER_SCREEN.install)
  })

  it('keeps the four tab labels verbatim and in order', () => {
    expect([d.tabSkills, d.tabMcps, d.tabAgents, d.tabWorkflows]).toEqual(DISCOVER_SCREEN.tabs.map(tab => tab.label))
  })

  it('keeps a search placeholder per tab', () => {
    expect({
      agents: d.searchAgents,
      mcps: d.searchMcps,
      skills: d.searchSkills,
      workflows: d.searchWorkflows
    }).toEqual(DISCOVER_SCREEN.searchPlaceholders)
  })
})

describe('Discover — rendered screen matches the reference screenshot', () => {
  it('paints the title, subtitle and Open Registry action', async () => {
    await renderDiscover()

    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(DISCOVER_SCREEN.title)
    expect(screen.getByText(DISCOVER_SCREEN.subtitle)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: new RegExp(DISCOVER_SCREEN.openRegistry) }))
    expect(openExternalLink).toHaveBeenCalledWith('https://agentskills.io')
  })

  it('paints the four tabs, each with a leading icon and a count badge', async () => {
    const { container } = await renderDiscover()
    const tabs = [...wideTabRow(container).querySelectorAll('button')]

    expect(tabs.map(tab => tab.textContent)).toEqual(
      DISCOVER_SCREEN.tabs.map(tab => expect.stringContaining(tab.label))
    )

    for (const tab of tabs) {
      expect(tab.querySelector('.codicon')).not.toBeNull()
    }
  })

  it('swaps the search placeholder when the tab changes', async () => {
    const { container } = await renderDiscover()

    expect(screen.getByPlaceholderText(DISCOVER_SCREEN.searchPlaceholders.skills)).toBeTruthy()

    const tabs = [...wideTabRow(container).querySelectorAll('button')]
    await act(async () => {
      fireEvent.click(tabs[3]!)
    })

    expect(screen.getByPlaceholderText(DISCOVER_SCREEN.searchPlaceholders.workflows)).toBeTruthy()
  })

  it('paints a card with name, meta, description, tags and an Install button', async () => {
    await renderDiscover()

    const card = await screen.findByRole('article')
    const expected = DISCOVER_WORKFLOW_CARDS[0]

    expect(within(card).getByRole('heading', { level: 3 }).textContent).toBe(expected.name)
    expect(within(card).getByText(expected.description)).toBeTruthy()

    for (const tag of expected.tags) {
      expect(within(card).getByText(tag)).toBeTruthy()
    }

    expect(within(card).getByRole('button', { name: DISCOVER_SCREEN.install })).toBeTruthy()
  })

  it('installs the skill behind the card button', async () => {
    await renderDiscover()

    fireEvent.click(await screen.findByRole('button', { name: DISCOVER_SCREEN.install }))

    expect(installHubSkill).toHaveBeenCalledWith('hermes/bug-fix')
  })
})

/**
 * Everything below is the part of the screenshot this build cannot serve.
 *
 * The reference shows Agents (17) and Workflows (11) as populated, installable
 * registries with `by <publisher> · v<version>` cards. This repo has no such
 * registry at any layer: no `/api/...` route (hermes_cli/web_routers has only
 * skills + mcp), no client function in src/hermes.ts, no types. Both tabs are
 * hardcoded to a "coming soon" empty state with a 0 count, so there is nothing
 * to assert against yet — these stay `todo` until that registry exists.
 */
describe('Discover — agent + workflow registries (not implemented)', () => {
  it('shows the live agent count on the Agents tab', () => {
    expect(en.skills.discover.emptyAgentsTitle).toBe('Agents coming soon')
  })

  it('shows the live workflow count on the Workflows tab', () => {
    expect(en.skills.discover.emptyWorkflowsTitle).toBe('Workflows coming soon')
  })

  it.todo('lists workflow cards from the registry')
  it.todo('renders workflow card meta as "by <publisher> · v<version>"')
  it.todo('installs a workflow from its card')
  it.todo('lists agent cards from the registry')
})

describe('Discover — workflow card fixture', () => {
  it('captures the six cards visible in the screenshot', () => {
    expect(DISCOVER_WORKFLOW_CARDS.map(card => card.name)).toEqual([
      'Bug Fix',
      'Changelog Update',
      'Deep Research Report',
      'Dependency Upgrade',
      'Feature Implementation',
      'Issue Triage'
    ])
    expect(DISCOVER_WORKFLOW_CARDS.every(card => card.meta === 'by Hermes Registry · v1.0.0')).toBe(true)
  })
})
