/**
 * Dashboard route → desktop (Hermes One) route.
 *
 * Every entry here is painted by the persistent DesktopChatHost, i.e. the web
 * UI shows the *same* Electron view the desktop app shows. Dashboard pages
 * only exist for surfaces the desktop app has no counterpart for (files, MCP,
 * pairing, docs, and the read-only admin pages).
 */
const DESKTOP_ROUTE_BY_DASHBOARD_PATH: Record<string, string> = {
  "/chat": "/",
  "/skills": "/skills",
  // Old /config bookmarks land on the desktop Settings overlay.
  "/config": "/settings",
  "/settings": "/settings",
  // Dashboard "Keys" → desktop Settings ▸ Keys
  "/env": "/settings?tab=keys",
  "/cron": "/cron",
  "/profiles": "/profiles",
  // The dashboard profile-builder wizard is gone; desktop Profiles owns create.
  "/profiles/new": "/profiles",
  "/webhooks": "/webhooks",
  // Dashboard "Channels" → desktop Office (messaging platforms).
  "/channels": "/messaging",
  "/artifacts": "/artifacts",
  "/agents": "/agents",
  "/starmap": "/starmap",
  "/command-center": "/command-center",
};

/**
 * Desktop views that render as a full-screen overlay card over the shell —
 * mirrors OVERLAY_VIEWS in apps/desktop/src/app/routes.ts. These are keyed
 * separately because closing an overlay navigates the desktop router back to
 * the chat, so the host has to remount its router to reopen the same path.
 * Note: these are DESKTOP paths, not dashboard ones.
 */
const DESKTOP_OVERLAY_ROUTES: ReadonlySet<string> = new Set([
  "/agents",
  "/command-center",
  "/cron",
  "/profiles",
  "/settings",
  "/starmap",
  "/webhooks",
]);

function normalize(pathname: string): string {
  return pathname.replace(/\/$/, "") || "/";
}

/** Map dashboard pathname → desktop MemoryRouter path (incl. search). */
export function dashboardPathToDesktop(pathname: string): string {
  return DESKTOP_ROUTE_BY_DASHBOARD_PATH[normalize(pathname)] ?? "/";
}

/** Routes painted by the persistent DesktopChatHost (not old dashboard pages). */
export function isDesktopEmbedPath(pathname: string): boolean {
  return normalize(pathname) in DESKTOP_ROUTE_BY_DASHBOARD_PATH;
}

/**
 * Does this DESKTOP path open an overlay view (vs a workspace page)?
 * `path` may carry a query, e.g. `/settings?tab=keys`.
 */
export function isDesktopOverlayRoute(path: string): boolean {
  const cut = path.search(/[?#]/);
  return DESKTOP_OVERLAY_ROUTES.has(normalize(cut === -1 ? path : path.slice(0, cut)));
}
