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

/** Built-in desktop primary-nav ids kept in standard mode (Chat). */
export const STANDARD_DESKTOP_PRIMARY_NAV_IDS = new Set(["new-session"]);

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
