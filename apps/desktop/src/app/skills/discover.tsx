import { useStore } from '@nanostores/react'
import { useQueries, useQuery } from '@tanstack/react-query'
import { type ComponentProps, useCallback, useMemo, useState } from 'react'

import { useDebounced } from '@/app/hooks/use-debounced'
import { useRouteEnumParam } from '@/app/hooks/use-route-enum-param'
import { PageLoader } from '@/components/page-loader'
import { Button } from '@/components/ui/button'
import { Codicon, codiconIcon } from '@/components/ui/codicon'
import { SearchField } from '@/components/ui/search-field'
import { ResponsiveTabs } from '@/components/ui/tab-dropdown'
import {
  getMcpCatalog,
  getSkillHubSources,
  installMcpCatalogEntry,
  type McpCatalogEntry,
  searchSkillsHub,
  type SkillHubResult
} from '@/hermes'
import { useI18n } from '@/i18n'
import { openExternalLink } from '@/lib/external-link'
import { Loader2 } from '@/lib/icons'
import { normalize } from '@/lib/text'
import { cn } from '@/lib/utils'
import {
  $hubActions,
  $hubInstalledOverride,
  HUB_SOURCES_KEY,
  installHubSkill,
  uninstallHubSkill
} from '@/store/hub-actions'
import { notify, notifyError } from '@/store/notifications'

const DISCOVER_TABS = ['skills', 'mcps', 'agents', 'workflows'] as const
type DiscoverTab = (typeof DISCOVER_TABS)[number]

const TRUST_RANK: Record<string, number> = { builtin: 2, trusted: 1, community: 0 }
const REGISTRY_URL = 'https://agentskills.io'
const MCP_CATALOG_KEY = ['mcp-catalog'] as const

interface DiscoverCardModel {
  description: string
  id: string
  installed: boolean
  installing?: boolean
  meta: string
  name: string
  onInstall?: () => void
  onUninstall?: () => void
  tags: string[]
}

function tabIcon(tab: DiscoverTab): string {
  switch (tab) {
    case 'mcps':
      return 'plug'
    case 'agents':
      return 'robot'
    case 'workflows':
      return 'type-hierarchy-sub'
    default:
      return 'extensions'
  }
}

function DiscoverCard({ card, icon }: { card: DiscoverCardModel; icon: string }) {
  const { t } = useI18n()
  const d = t.skills.discover
  const busy = Boolean(card.installing)

  return (
    <article className="flex flex-col rounded-xl border border-(--ui-stroke-tertiary) bg-(--ui-bg-secondary)/40 p-4 transition-colors hover:border-(--ui-stroke-secondary) hover:bg-(--ui-bg-secondary)/70">
      <div className="flex items-start gap-3">
        <div className="grid size-10 shrink-0 place-items-center rounded-lg bg-sky-500/15 text-sky-400">
          <Codicon name={icon} size="1.15rem" />
        </div>
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-[0.9rem] font-semibold text-foreground">{card.name}</h3>
          <p className="mt-0.5 truncate text-[0.68rem] text-(--ui-text-tertiary)">{card.meta}</p>
        </div>
      </div>

      <p className="mt-3 line-clamp-2 min-h-[2.4em] text-[0.78rem] leading-snug text-(--ui-text-secondary)">
        {card.description || d.noDescription}
      </p>

      {card.tags.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {card.tags.slice(0, 4).map(tag => (
            <span
              className="rounded-md bg-(--ui-bg-tertiary) px-1.5 py-0.5 text-[0.62rem] text-(--ui-text-tertiary)"
              key={tag}
            >
              {tag}
            </span>
          ))}
        </div>
      )}

      <div className="mt-4 flex justify-end">
        {card.installed ? (
          <Button
            className="min-w-20"
            disabled={busy || !card.onUninstall}
            onClick={card.onUninstall}
            size="sm"
            variant="secondary"
          >
            {busy && <Loader2 className="size-3 animate-spin" />}
            {busy ? d.uninstalling : d.installed}
          </Button>
        ) : (
          <Button className="min-w-20" disabled={busy || !card.onInstall} onClick={card.onInstall} size="sm">
            {busy && <Loader2 className="size-3 animate-spin" />}
            {busy ? d.installing : d.install}
          </Button>
        )}
      </div>
    </article>
  )
}

function DiscoverEmpty({ description, title }: { description: string; title: string }) {
  return (
    <div className="grid min-h-56 place-items-center px-6 text-center">
      <div className="max-w-md space-y-1.5">
        <p className="text-[0.9rem] font-medium text-foreground/90">{title}</p>
        <p className="text-[0.75rem] text-(--ui-text-tertiary)">{description}</p>
      </div>
    </div>
  )
}

function SkillDiscoverCards({ query }: { query: string }) {
  const { t } = useI18n()
  const h = t.skills.hub
  const d = t.skills.discover
  const term = useDebounced(query.trim(), 350)
  const actions = useStore($hubActions)
  const overrides = useStore($hubInstalledOverride)

  const sourcesQuery = useQuery({
    queryKey: HUB_SOURCES_KEY,
    queryFn: getSkillHubSources,
    staleTime: 5 * 60_000
  })

  const searchableSources = useMemo(
    () => (sourcesQuery.data?.sources ?? []).filter(source => source.searchable !== false),
    [sourcesQuery.data]
  )

  const sourceSearches = useQueries({
    queries: searchableSources.map(source => ({
      queryKey: ['skill-hub-search', term, source.id],
      queryFn: () => searchSkillsHub(term, source.id, 50),
      enabled: term.length > 0,
      staleTime: 60_000
    }))
  })

  const results = useMemo(() => {
    const seen = new Map<string, SkillHubResult>()

    for (const q of sourceSearches) {
      for (const r of q.data?.results ?? []) {
        const prev = seen.get(r.identifier)

        if (!prev || (TRUST_RANK[r.trust_level] ?? 0) > (TRUST_RANK[prev.trust_level] ?? 0)) {
          seen.set(r.identifier, r)
        }
      }
    }

    return [...seen.values()].sort(
      (a, b) => (TRUST_RANK[b.trust_level] ?? 0) - (TRUST_RANK[a.trust_level] ?? 0) || a.name.localeCompare(b.name)
    )
  }, [sourceSearches])

  const installed = { ...(sourcesQuery.data?.installed ?? {}) }

  for (const q of sourceSearches) {
    Object.assign(installed, q.data?.installed ?? {})
  }

  const featured = sourcesQuery.data?.featured ?? []
  const listed = term.length === 0 ? featured : results
  const anyFetching = term.length > 0 && sourceSearches.some(q => q.isFetching)
  const searching = anyFetching && results.length === 0

  if (sourcesQuery.isLoading || searching) {
    return <PageLoader className="min-h-56" label={h.searching} />
  }

  if (sourcesQuery.isError) {
    return <DiscoverEmpty description={h.loadFailed} title={d.emptySkillsTitle} />
  }

  if (listed.length === 0) {
    return (
      <DiscoverEmpty
        description={term ? h.noResults : h.landingHint}
        title={term ? d.emptySearchTitle : d.emptySkillsTitle}
      />
    )
  }

  const cards: DiscoverCardModel[] = listed.map(skill => {
    const override = overrides[skill.identifier]
    const rawInstalled = Boolean(installed[skill.identifier])
    const isInstalled = override ?? rawInstalled
    const running = actions[skill.identifier]?.running ?? false
    const trust = h.trust[skill.trust_level] ?? skill.trust_level
    const sourceLabel = skill.source || 'Hermes Registry'

    return {
      description: skill.description,
      id: skill.identifier,
      installed: isInstalled,
      installing: running,
      meta: `${sourceLabel} · ${trust}`,
      name: skill.name,
      onInstall: () => {
        notify({ kind: 'success', title: h.installStarted(skill.name), message: h.actionLog })
        void installHubSkill(skill.identifier).catch(err => notifyError(err, h.actionFailed))
      },
      onUninstall: () => {
        const name = installed[skill.identifier]?.name || skill.name
        notify({ kind: 'success', title: h.uninstallStarted(skill.name), message: h.actionLog })
        void uninstallHubSkill(skill.identifier, name).catch(err => notifyError(err, h.actionFailed))
      },
      tags: skill.tags ?? []
    }
  })

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {cards.map(card => (
        <DiscoverCard card={card} icon={tabIcon('skills')} key={card.id} />
      ))}
    </div>
  )
}

function McpDiscoverCards({ query }: { query: string }) {
  const { t } = useI18n()
  const m = t.settings.mcp
  const d = t.skills.discover
  const [installing, setInstalling] = useState<null | string>(null)

  const catalogQuery = useQuery({
    queryKey: MCP_CATALOG_KEY,
    queryFn: getMcpCatalog,
    staleTime: 5 * 60_000
  })

  const q = normalize(query)
  const entries = useMemo(() => {
    const all = catalogQuery.data?.entries ?? []

    if (!q) {
      return all
    }

    return all.filter(
      entry =>
        normalize(entry.name).includes(q) ||
        normalize(entry.description).includes(q) ||
        normalize(entry.transport).includes(q) ||
        normalize(entry.source).includes(q)
    )
  }, [catalogQuery.data, q])

  const install = useCallback(
    async (entry: McpCatalogEntry) => {
      if (entry.required_env.some(env => env.required)) {
        notify({
          kind: 'error',
          title: m.catalogEnvPrompt(entry.name),
          message: d.mcpNeedsCredentials
        })

        return
      }

      setInstalling(entry.name)

      try {
        await installMcpCatalogEntry(entry.name, {})
        notify({ kind: 'success', title: m.catalogInstallStarted(entry.name), message: '' })
        void catalogQuery.refetch()
      } catch (err) {
        notifyError(err, m.catalogInstallFailed(entry.name))
      } finally {
        setInstalling(null)
      }
    },
    [catalogQuery, d.mcpNeedsCredentials, m]
  )

  if (catalogQuery.isLoading) {
    return <PageLoader className="min-h-56" label={m.catalogLoading} />
  }

  if (catalogQuery.isError) {
    return <DiscoverEmpty description={m.catalogEmpty} title={d.emptyMcpsTitle} />
  }

  if (entries.length === 0) {
    return (
      <DiscoverEmpty
        description={q ? d.emptySearchDesc : m.catalogEmpty}
        title={q ? d.emptySearchTitle : d.emptyMcpsTitle}
      />
    )
  }

  const cards: DiscoverCardModel[] = entries.map(entry => ({
    description: entry.description,
    id: entry.name,
    installed: entry.installed,
    installing: installing === entry.name,
    meta: `${entry.source || 'Hermes Catalog'} · ${entry.transport}`,
    name: entry.name,
    onInstall: entry.installed ? undefined : () => void install(entry),
    tags: [entry.auth_type, entry.transport].filter(Boolean)
  }))

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {cards.map(card => (
        <DiscoverCard card={card} icon={tabIcon('mcps')} key={card.id} />
      ))}
    </div>
  )
}

export function DiscoverView({ className, ...props }: ComponentProps<'section'>) {
  const { t } = useI18n()
  const d = t.skills.discover
  const [tab, setTab] = useRouteEnumParam('tab', DISCOVER_TABS, 'skills')
  const [query, setQuery] = useState('')

  const sourcesQuery = useQuery({
    queryKey: HUB_SOURCES_KEY,
    queryFn: getSkillHubSources,
    staleTime: 5 * 60_000
  })

  const catalogQuery = useQuery({
    queryKey: MCP_CATALOG_KEY,
    queryFn: getMcpCatalog,
    staleTime: 5 * 60_000
  })

  const skillCount = sourcesQuery.isLoading ? null : (sourcesQuery.data?.featured.length ?? 0)
  const mcpCount = catalogQuery.isLoading ? null : (catalogQuery.data?.entries.length ?? 0)

  const refresh = () => {
    void sourcesQuery.refetch()
    void catalogQuery.refetch()
  }

  const searchPlaceholder =
    tab === 'skills'
      ? d.searchSkills
      : tab === 'mcps'
        ? d.searchMcps
        : tab === 'agents'
          ? d.searchAgents
          : d.searchWorkflows

  return (
    <section
      {...props}
      className={cn('flex h-full min-w-0 flex-col overflow-hidden bg-(--ui-chat-surface-background)', className)}
    >
      <div className="shrink-0 px-5 pt-[calc(var(--titlebar-height)+1rem)] pb-4 md:px-8">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-[1.65rem] font-semibold tracking-tight text-foreground">{d.title}</h1>
            <p className="mt-1 text-[0.85rem] text-(--ui-text-tertiary)">{d.subtitle}</p>
          </div>
          <Button onClick={() => openExternalLink(REGISTRY_URL)} size="sm" variant="secondary">
            <Codicon name="link-external" size="0.75rem" />
            {d.openRegistry}
          </Button>
        </div>

        <div className="mt-5">
          <ResponsiveTabs
            onChange={id => {
              setTab(id as DiscoverTab)
              setQuery('')
            }}
            tabs={[
              { icon: codiconIcon(tabIcon('skills')), id: 'skills', label: d.tabSkills, meta: skillCount },
              { icon: codiconIcon(tabIcon('mcps')), id: 'mcps', label: d.tabMcps, meta: mcpCount },
              { icon: codiconIcon(tabIcon('agents')), id: 'agents', label: d.tabAgents, meta: 0 },
              { icon: codiconIcon(tabIcon('workflows')), id: 'workflows', label: d.tabWorkflows, meta: 0 }
            ]}
            value={tab}
            wideClassName="justify-start gap-x-4"
          />
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <SearchField
            containerClassName="min-w-[14rem] flex-1"
            onChange={setQuery}
            placeholder={searchPlaceholder}
            value={query}
          />
          <Button onClick={refresh} size="sm" variant="secondary">
            <Codicon name="refresh" size="0.8rem" />
            {d.refresh}
          </Button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-8 md:px-8 [scrollbar-gutter:stable]">
        {tab === 'skills' && <SkillDiscoverCards query={query} />}
        {tab === 'mcps' && <McpDiscoverCards query={query} />}
        {tab === 'agents' && <DiscoverEmpty description={d.emptyAgentsDesc} title={d.emptyAgentsTitle} />}
        {tab === 'workflows' && <DiscoverEmpty description={d.emptyWorkflowsDesc} title={d.emptyWorkflowsTitle} />}
      </div>
    </section>
  )
}
