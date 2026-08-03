/** Map dashboard pathname → desktop MemoryRouter path (incl. search). */
export function dashboardPathToDesktop(pathname: string): string {
  const normalized = pathname.replace(/\/$/, "") || "/";
  switch (normalized) {
    case "/skills":
      return "/skills";
    case "/config":
      return "/settings";
    case "/settings":
      return "/settings";
    case "/env":
      // Dashboard "Keys" → desktop Settings ▸ Keys
      return "/settings?tab=keys";
    case "/chat":
    default:
      return "/";
  }
}

/** Routes painted by the persistent DesktopChatHost (not old dashboard pages). */
export function isDesktopEmbedPath(pathname: string): boolean {
  const normalized = pathname.replace(/\/$/, "") || "/";
  return (
    normalized === "/chat" ||
    normalized === "/skills" ||
    normalized === "/config" ||
    normalized === "/settings" ||
    normalized === "/env"
  );
}
