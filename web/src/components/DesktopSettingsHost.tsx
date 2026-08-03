/**
 * Mounts the Electron desktop SettingsView 1:1 — same module as
 * `apps/desktop/src/app/settings`. No dashboard ConfigPage, no shell chrome.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  StrictMode,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ComponentType,
} from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";

import { installHermesDesktopStub } from "@/lib/hermesDesktopStub";

import "@desktop/styles.css";
import "./hermes-one-web.css";

/** Iris aubergine — match DesktopChatHost / irisTheme */
const BOOT_BG = "#2a0f2e";
const BOOT_FG = "#f4ede4";

type SettingsViewProps = {
  onClose: () => void;
  gateway?: unknown;
  onConfigSaved?: () => void;
  onMainModelChanged?: (provider: string, model: string) => void;
};

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

function preferDesktopTheme(): void {
  try {
    window.localStorage.setItem("hermes-desktop-theme-v2", "iris");
    window.localStorage.setItem("hermes-desktop-mode-v1", "dark");
    window.localStorage.setItem("hermes-boot-background", BOOT_BG);
    window.localStorage.setItem("hermes-boot-color-scheme", "dark");
    document.documentElement.classList.add("dark");
    document.documentElement.style.colorScheme = "dark";
    document.documentElement.style.backgroundColor = BOOT_BG;
    document.documentElement.style.color = BOOT_FG;
  } catch {
    // private browsing
  }
}

function SettingsTree({
  SettingsView,
  I18nProvider,
  ThemeProvider,
  HapticsProvider,
  RootTooltipProvider,
  path,
  onClose,
}: {
  SettingsView: ComponentType<SettingsViewProps>;
  I18nProvider: ComponentType<{ children: React.ReactNode }>;
  ThemeProvider: ComponentType<{ children: React.ReactNode }>;
  HapticsProvider: ComponentType<{ children: React.ReactNode }>;
  RootTooltipProvider: ComponentType<{ children: React.ReactNode }>;
  path: string;
  onClose: () => void;
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
          position: "relative",
        }}
      >
        <QueryClientProvider client={getQueryClient()}>
          <I18nProvider>
            <ThemeProvider>
              <HapticsProvider>
                <RootTooltipProvider>
                  <MemoryRouter
                    key={path}
                    initialEntries={[path]}
                    useTransitions={false}
                  >
                    <SettingsView onClose={onClose} />
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

/**
 * Full-bleed Electron Settings. `path` is a desktop settings URL
 * (`/settings` or `/settings?tab=keys`).
 */
export default function DesktopSettingsHost({
  isActive,
  path = "/settings",
  onClose,
}: {
  isActive: boolean;
  path?: string;
  onClose: () => void;
}) {
  const mountRef = useRef<HTMLDivElement>(null);
  const rootRef = useRef<Root | null>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const modsRef = useRef<{
    SettingsView: ComponentType<SettingsViewProps>;
    I18nProvider: ComponentType<{ children: React.ReactNode }>;
    ThemeProvider: ComponentType<{ children: React.ReactNode }>;
    HapticsProvider: ComponentType<{ children: React.ReactNode }>;
    RootTooltipProvider: ComponentType<{ children: React.ReactNode }>;
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
        const [settingsMod, i18n, themes, haptics, tooltip] = await Promise.all([
          import("@desktop/app/settings"),
          import("@desktop/i18n"),
          import("@desktop/themes/context"),
          import("@desktop/components/haptics-provider"),
          import("@desktop/components/ui/tooltip"),
        ]);
        if (cancelled) return;

        const mods = {
          SettingsView: settingsMod.SettingsView,
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
        next.render(
          <SettingsTree
            {...mods}
            path={path}
            onClose={() => onCloseRef.current()}
          />,
        );
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!ready || !rootRef.current || !modsRef.current) return;
    rootRef.current.render(
      <SettingsTree
        {...modsRef.current}
        path={path}
        onClose={() => onCloseRef.current()}
      />,
    );
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
      data-desktop-settings-host=""
      data-desktop-embed=""
      data-desktop-theme="iris"
      data-desktop-mode="dark"
    >
      {error ? (
        <div
          className="absolute inset-0 z-10 flex items-center justify-center p-8 text-sm"
          role="alert"
        >
          Desktop Settings failed to load: {error}
        </div>
      ) : !ready ? (
        <div
          className="absolute inset-0 z-10 flex items-center justify-center p-8 text-sm opacity-70"
          aria-busy="true"
        >
          Loading Settings…
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
