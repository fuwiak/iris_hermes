/** Dashboard / desktop left-nav density: shop-default vs full Hermes. */

export type NavMode = "pro" | "standard";

/** localStorage key — shared by web dashboard chrome and desktop embed sidebar. */
export const NAV_MODE_STORAGE_KEY = "hermes-nav-mode";

/** Same-window sync when desktop embed and dashboard chrome both mount. */
export const NAV_MODE_CHANGE_EVENT = "hermes-nav-mode-change";

export const DEFAULT_NAV_MODE: NavMode = "standard";

/** Plugin manifest / desktop plugin `id` values kept visible in standard mode. */
export const STANDARD_NAV_PLUGIN_NAMES = new Set(["moysklad"]);

/**
 * MoySklad (and future allowlisted plugin) paths kept in standard mode
 * (Клиенты / Рассылки). Query/hash stripped before matching.
 */
export const STANDARD_NAV_PLUGIN_PATHS = new Set(["/campaigns", "/clients"]);

/**
 * Seed rows for standard mode when contributions/manifests are late or missing.
 * Desktop + web both use these so Клиенты/Рассылки cannot disappear.
 */
export const STANDARD_MOYSKLAD_NAV_ITEMS = [
  { path: "/clients", label: "Клиенты", codicon: "organization", icon: "Users" },
  { path: "/campaigns", label: "Рассылки", codicon: "mail", icon: "Mail" },
] as const;

/**
 * Web dashboard built-in core paths kept in standard mode
 * (Chat + Settings).
 */
export const STANDARD_WEB_CORE_PATHS = new Set(["/chat", "/settings"]);

/**
 * Built-in desktop primary-nav ids kept in standard mode
 * (Chat + Settings via embed app-control).
 *
 * Embed remaps titlebar tools to `app-control-${tool.id}` — use
 * {@link isStandardDesktopPrimaryNavId} when filtering, not this Set alone.
 */
export const STANDARD_DESKTOP_PRIMARY_NAV_IDS = new Set([
  "new-session",
  "settings",
]);

const APP_CONTROL_NAV_PREFIX = "app-control-";

/** Pathname only — strips `?query` / `#hash` before allowlist checks. */
export function navPathname(path: string): string {
  const cut = path.search(/[?#]/);
  const raw = cut === -1 ? path : path.slice(0, cut);
  return raw.replace(/\/$/, "") || "/";
}

/** True for MoySklad Клиенты/Рассылки (and any other STANDARD_NAV_PLUGIN_PATHS). */
export function isStandardNavPluginPath(path: string): boolean {
  return STANDARD_NAV_PLUGIN_PATHS.has(navPathname(path));
}

/**
 * True when a sidebar contribution belongs to an allowlisted plugin
 * (by path, `plugin:<id>` source, or namespaced contribution id).
 * Paths outside {@link STANDARD_NAV_PLUGIN_PATHS} stay hidden in standard
 * even if the source is moysklad (e.g. Plugins → `/settings?tab=plugins`).
 */
export function isStandardNavPluginContribution(opts: {
  id?: string;
  path?: string;
  source?: string;
}): boolean {
  const path = opts.path;
  if (!path || !isStandardNavPluginPath(path)) return false;

  const source = opts.source;
  if (source) {
    for (const name of STANDARD_NAV_PLUGIN_NAMES) {
      if (source === `plugin:${name}`) return true;
    }
  }

  const id = opts.id;
  if (id) {
    for (const name of STANDARD_NAV_PLUGIN_NAMES) {
      if (id === name || id.startsWith(`${name}:`)) return true;
    }
  }

  // Path allowlist alone is enough (web nav items have no plugin source).
  return true;
}

/** True for Chat (`new-session`) and Settings (`settings` / `app-control-settings`). */
export function isStandardDesktopPrimaryNavId(id: string): boolean {
  if (STANDARD_DESKTOP_PRIMARY_NAV_IDS.has(id)) return true;
  if (id.startsWith(APP_CONTROL_NAV_PREFIX)) {
    return STANDARD_DESKTOP_PRIMARY_NAV_IDS.has(
      id.slice(APP_CONTROL_NAV_PREFIX.length),
    );
  }
  return false;
}

export function parseNavMode(raw: null | string | undefined): NavMode {
  return raw === "pro" ? "pro" : "standard";
}

export function readNavMode(): NavMode {
  try {
    return parseNavMode(window.localStorage.getItem(NAV_MODE_STORAGE_KEY));
  } catch {
    return DEFAULT_NAV_MODE;
  }
}

export function writeNavMode(mode: NavMode): void {
  try {
    window.localStorage.setItem(NAV_MODE_STORAGE_KEY, mode);
  } catch {
    // private browsing / blocked storage
  }

  try {
    window.dispatchEvent(
      new CustomEvent(NAV_MODE_CHANGE_EVENT, { detail: mode }),
    );
  } catch {
    // non-DOM test envs
  }
}
