/** Dashboard / desktop left-nav density: shop-default vs full Hermes. */

export type NavMode = "pro" | "standard";

/** localStorage key — shared by web dashboard chrome and desktop embed sidebar. */
export const NAV_MODE_STORAGE_KEY = "hermes-nav-mode";

/** Same-window sync when desktop embed and dashboard chrome both mount. */
export const NAV_MODE_CHANGE_EVENT = "hermes-nav-mode-change";

export const DEFAULT_NAV_MODE: NavMode = "standard";

/** Plugin manifest `name` values kept visible in standard mode. */
export const STANDARD_NAV_PLUGIN_NAMES = new Set(["moysklad"]);

/**
 * Desktop `sidebar.nav` contribution paths kept in standard mode
 * (MoySklad Клиенты / Рассылки).
 */
export const STANDARD_NAV_PLUGIN_PATHS = new Set(["/campaigns", "/clients"]);

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
