import type { ComponentProps } from 'react'

import type { SetStatusbarItemGroup } from '@/app/shell/statusbar-controls'

import { DiscoverView } from './discover'

interface SkillsViewProps extends ComponentProps<'section'> {
  setStatusbarItemGroup?: SetStatusbarItemGroup
}

/** Hermes One only — Discover is the /skills surface. Stock CapabilitiesView removed. */
export function SkillsView({ setStatusbarItemGroup: _setStatusbarItemGroup, ...props }: SkillsViewProps) {
  return <DiscoverView {...props} />
}
