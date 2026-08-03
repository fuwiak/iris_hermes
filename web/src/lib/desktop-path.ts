/** Map dashboard pathname → desktop MemoryRouter path. */
export function dashboardPathToDesktop(pathname: string): string {
  const normalized = pathname.replace(/\/$/, "") || "/";
  switch (normalized) {
    case "/skills":
      return "/skills";
    case "/chat":
    default:
      return "/";
  }
}
