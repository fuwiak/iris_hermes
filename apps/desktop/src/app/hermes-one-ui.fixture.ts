/**
 * Text extracted verbatim from the two Hermes One reference screenshots.
 *
 * This module is the spec: `*.test.tsx` files assert both that the English
 * catalog still says exactly this AND that the components render it. Changing
 * a string here is a deliberate design change, not a refactor.
 *
 * Screenshot A — Discover (`/skills`), Workflows tab selected.
 * Screenshot B — new chat (`/`), Hermes One shell.
 */

/** Screenshot A — page chrome. */
export const DISCOVER_SCREEN = {
  title: 'Discover',
  subtitle: 'Browse community skills, MCP servers, agents, and workflows.',
  openRegistry: 'Open Registry',
  refresh: 'Refresh',
  install: 'Install',
  /** Tab label + the count badge beside it. */
  tabs: [
    { label: 'Skills', count: 349 },
    { label: 'MCPs', count: 55 },
    { label: 'Agents', count: 17 },
    { label: 'Workflows', count: 11 }
  ],
  searchPlaceholders: {
    skills: 'Search skills...',
    mcps: 'Search MCPs...',
    agents: 'Search agents...',
    workflows: 'Search workflows...'
  }
} as const

/**
 * Screenshot A — the six workflow cards visible above the fold.
 *
 * NOTE: nothing in this repo can serve these. There is no workflow (or agent)
 * registry: no `/api/…` route, no client function, no types. The Workflows and
 * Agents tabs render a "coming soon" empty state instead. The cards below are
 * kept as the target contract for whoever wires that registry up — see the
 * `it.todo` cases in `app/skills/discover.test.tsx`.
 */
export const DISCOVER_WORKFLOW_CARDS = [
  {
    name: 'Bug Fix',
    meta: 'by Hermes Registry · v1.0.0',
    description: 'Reproduce a reported bug, fix it, and add a regression test.',
    tags: ['debugging', 'testing']
  },
  {
    name: 'Changelog Update',
    meta: 'by Hermes Registry · v1.0.0',
    description: 'Update CHANGELOG.md from recent commits following Keep a Changelog.',
    tags: ['docs', 'changelog']
  },
  {
    name: 'Deep Research Report',
    meta: 'by Hermes Registry · v1.0.0',
    description: 'Research a question across arXiv and the web, then write a cited report.',
    tags: ['research', 'report', 'writing']
  },
  {
    name: 'Dependency Upgrade',
    meta: 'by Hermes Registry · v1.0.0',
    description: 'Bump dependencies, run the test suite, and open a PR if green.',
    tags: ['maintenance', 'dependencies']
  },
  {
    name: 'Feature Implementation',
    meta: 'by Hermes Registry · v1.0.0',
    description: 'Plan, implement, and test a new feature end to end.',
    tags: ['development', 'feature']
  },
  {
    name: 'Issue Triage',
    meta: 'by Hermes Registry · v1.0.0',
    description: 'Label, prioritize, and route incoming GitHub issues.',
    tags: ['github', 'automation', 'triage']
  }
] as const

/** Screenshot B — the new-chat intro. */
export const INTRO_SCREEN = {
  /** The round black mark stacks these two words. */
  mark: ['HERMES', 'ONE'],
  title: 'How can I help you today?',
  subtitle: 'Ask me to write code, answer questions, search the web, and more',
  /** Suggestion chips, in the order they wrap on screen. */
  suggestions: [
    'Search the web',
    'Set a reminder',
    'Summarize emails',
    'Write a script',
    'Schedule a cron job',
    'Analyze data'
  ]
} as const

/** Screenshot B — sidebar primary nav, top to bottom. */
export const SIDEBAR_SCREEN = {
  nav: ['New Chat', 'Discover', 'Office', 'Kanban', 'Schedules'],
  sessionsSection: 'Chats'
} as const

/**
 * Screenshot B — composer placeholder. It rotates per session out of
 * `composer.newSessionPlaceholders`; the screenshot caught this one, so the
 * only durable assertion is that it is still in that pool.
 */
export const COMPOSER_SCREEN = {
  placeholder: 'Ask anything'
} as const
