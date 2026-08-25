/**
 * Tripwire re-export of the `use-sync-external-store` shim package —
 * react-arborist (files tree) and friends import useSyncExternalStore from
 * here instead of `react`, bypassing the react alias. Route them through the
 * same per-instance loop detector. Temporary diagnostics.
 */
// @ts-nocheck
import { useSyncExternalStore } from "./react-uses-tripwire";

export { useSyncExternalStore };
export default { useSyncExternalStore };
