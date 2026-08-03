/**
 * Embeds Hermes One (desktop renderer) in the dashboard — Electron look + feel.
 * Separate React root so dashboard BrowserRouter does not nest MemoryRouter.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  StrictMode,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useNavigate } from "react-router";

import { installHermesDesktopStub } from "@/lib/hermesDesktopStub";

import "@desktop/styles.css";
import "./hermes-one-web.css";

/** Match Electron Hermes One charcoal chrome — apps/desktop/src/themes/presets.ts */
const WEB_DESKTOP_SKIN = "mono";
const WEB_DESKTOP_MODE = "dark";
const BOOT_BG = "#0e0e0e";
const BOOT_FG = "#eaeaea";

function preferDesktopTheme(): void {
  try {
    window.localStorage.setItem("hermes-desktop-theme-v2", WEB_DESKTOP_SKIN);
    window.localStorage.setItem("hermes-desktop-mode-v1", WEB_DESKTOP_MODE);
    window.localStorage.setItem("hermes-boot-background", BOOT_BG);
    window.localStorage.setItem("hermes-boot-color-scheme", "dark");
    document.documentElement.classList.add("dark");
    document.documentElement.style.colorScheme = "dark";
    document.documentElement.style.backgroundColor = BOOT_BG;
    document.documentElement.style.color = BOOT_FG;
  } catch {
    // private browsing / blocked storage
  }
}

let queryClient: QueryClient | null = null;
function getQueryClient(): QueryClient {
  if (!queryClient) {
    queryClient = new QueryClient({
      defaultOptions: {
        queries: { retry: false, refetchOnWindowFocus: false },
      },
    });
  }
  return queryClient;
}

/**
 * Sync dashboard → desktop ONLY when the dashboard path prop changes.
 * Never re-assert on every render — that wiped in-app sidebar navigation.
 */
function DesktopRouteSync({ path }: { path: string }) {
  const navigate = useNavigate();
  const lastSynced = useRef<string | null>(null);

  useEffect(() => {
    if (lastSynced.current === path) return;
    lastSynced.current = path;
    navigate(path, { replace: true });
  }, [navigate, path]);

  return null;
}

function DesktopTree({
  DesktopApp,
  I18nProvider,
  ThemeProvider,
  HapticsProvider,
  RootTooltipProvider,
  path,
}: {
  DesktopApp: React.ComponentType;
  I18nProvider: React.ComponentType<{ children: React.ReactNode }>;
  ThemeProvider: React.ComponentType<{ children: React.ReactNode }>;
  HapticsProvider: React.ComponentType<{ children: React.ReactNode }>;
  RootTooltipProvider: React.ComponentType<{ children: React.ReactNode }>;
  path: string;
}) {
  return (
    <StrictMode>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          width: "100%",
          height: "100%",
          minWidth: 0,
          minHeight: 0,
          flex: 1,
          overflow: "hidden",
          backgroundColor: BOOT_BG,
          color: BOOT_FG,
        }}
      >
        <QueryClientProvider client={getQueryClient()}>
          <I18nProvider>
            <ThemeProvider>
              <HapticsProvider>
                <RootTooltipProvider>
                  <MemoryRouter initialEntries={[path]} useTransitions={false}>
                    <DesktopRouteSync path={path} />
                    <DesktopApp />
                  </MemoryRouter>
                </RootTooltipProvider>
              </HapticsProvider>
            </ThemeProvider>
          </I18nProvider>
        </QueryClientProvider>
      </div>
    </StrictMode>
  );
}

export default function DesktopChatHost({
  isActive,
  path = "/",
}: {
  isActive: boolean;
  /** Desktop-internal path (e.g. `/`, `/skills`). */
  path?: string;
}) {
  const mountRef = useRef<HTMLDivElement>(null);
  const rootRef = useRef<Root | null>(null);
  const modsRef = useRef<{
    DesktopApp: React.ComponentType;
    I18nProvider: React.ComponentType<{ children: React.ReactNode }>;
    ThemeProvider: React.ComponentType<{ children: React.ReactNode }>;
    HapticsProvider: React.ComponentType<{ children: React.ReactNode }>;
    RootTooltipProvider: React.ComponentType<{ children: React.ReactNode }>;
  } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const el = mountRef.current;
    if (!el) return;

    preferDesktopTheme();
    installHermesDesktopStub();

    let cancelled = false;

    void (async () => {
      try {
        preferDesktopTheme();
        const [{ default: DesktopApp }, i18n, themes, haptics, tooltip] =
          await Promise.all([
            import("@desktop/app"),
            import("@desktop/i18n"),
            import("@desktop/themes/context"),
            import("@desktop/components/haptics-provider"),
            import("@desktop/components/ui/tooltip"),
          ]);
        if (cancelled) return;

        const mods = {
          DesktopApp,
          I18nProvider: i18n.I18nProvider,
          ThemeProvider: themes.ThemeProvider,
          HapticsProvider: haptics.HapticsProvider,
          RootTooltipProvider: tooltip.RootTooltipProvider,
        };
        modsRef.current = mods;

        const next = createRoot(el);
        if (cancelled) {
          next.unmount();
          return;
        }
        rootRef.current = next;
        next.render(<DesktopTree {...mods} path={path} />);
        setError(null);
        setReady(true);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
          setReady(false);
        }
      }
    })();

    return () => {
      cancelled = true;
      setReady(false);
      rootRef.current?.unmount();
      rootRef.current = null;
      modsRef.current = null;
    };
    // Boot once — path updates paint via the effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!ready || !rootRef.current || !modsRef.current) return;
    rootRef.current.render(<DesktopTree {...modsRef.current} path={path} />);
  }, [path, ready]);

  const style = useMemo(
    () =>
      ({
        display: isActive ? "flex" : "none",
        flex: 1,
        minHeight: 0,
        minWidth: 0,
        width: "100%",
        height: "100%",
        flexDirection: "column" as const,
        isolation: "isolate" as const,
        // Contain `position:fixed` titlebar tools inside the host (not the
        // browser viewport) so they can't steal clicks from the sidebar.
        transform: "translateZ(0)",
        backgroundColor: BOOT_BG,
        color: BOOT_FG,
        colorScheme: "dark" as const,
        overflow: "hidden" as const,
        position: "relative" as const,
      }) satisfies CSSProperties,
    [isActive],
  );

  return (
    <div
      style={style}
      className="dark"
      data-desktop-chat-host=""
      data-desktop-embed=""
      data-desktop-theme="mono"
      data-desktop-mode="dark"
      data-chat-active={isActive ? "true" : "false"}
    >
      {error ? (
        <div
          className="absolute inset-0 z-10 flex items-center justify-center p-8 text-sm"
          role="alert"
        >
          Desktop app failed to load: {error}
        </div>
      ) : !ready ? (
        <div
          className="absolute inset-0 z-10 flex items-center justify-center p-8 text-sm opacity-70"
          aria-busy="true"
        >
          Loading Hermes…
        </div>
      ) : null}
      <div
        ref={mountRef}
        className="dark"
        style={{
          display: "flex",
          flexDirection: "column",
          flex: 1,
          minHeight: 0,
          minWidth: 0,
          width: "100%",
          height: "100%",
          backgroundColor: BOOT_BG,
          color: BOOT_FG,
          overflow: "hidden",
        }}
      />
    </div>
  );
}
