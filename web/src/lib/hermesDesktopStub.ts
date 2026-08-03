/**
 * Browser stand-in for Electron `window.hermesDesktop`.
 * Lets the desktop React chat boot against the dashboard gateway
 * (`/api/*` REST + `/api/ws` JSON-RPC) instead of IPC.
 */

import {
  buildHermesWebSocketUrl,
  type GatewayWsUrlResult,
} from "@hermes/shared";

import {
  HERMES_BASE_PATH,
  buildWsAuthParam,
} from "@/lib/api";

type HermesApiRequest = {
  path: string;
  method?: string;
  body?: unknown;
  upload?: { filename: string; contentType?: string; bytes: ArrayBuffer };
  timeoutMs?: number;
  profile?: string | null;
};

type HermesConnection = {
  baseUrl: string;
  isFullscreen: boolean;
  mode: "local" | "remote";
  authMode: "oauth" | "token";
  nativeOverlayWidth: number;
  source: "local";
  token: string;
  wsUrl: string;
  logs: string[];
  windowButtonPosition: null;
};

const SESSION_HEADER = "X-Hermes-Session-Token";

function baseUrl(): string {
  return `${window.location.origin}${HERMES_BASE_PATH}`;
}

function sessionToken(): string {
  return window.__HERMES_SESSION_TOKEN__ ?? "";
}

async function mintWsUrl(): Promise<string> {
  const authParam = await buildWsAuthParam();
  return buildHermesWebSocketUrl({
    authParam,
    basePath: HERMES_BASE_PATH,
    path: "/api/ws",
  });
}

function emptyBootProgress() {
  return {
    error: null as string | null,
    fakeMode: false,
    message: "Ready",
    phase: "ready",
    progress: 100,
    running: false,
    timestamp: Date.now(),
  };
}

/** Electron IPC routes via `request.profile`; dashboard REST uses `?profile=`. */
function pathWithProfile(path: string, profile?: string | null): string {
  const scoped = (profile ?? "").trim();
  if (!scoped || path.includes("profile=")) return path;
  return `${path}${path.includes("?") ? "&" : "?"}profile=${encodeURIComponent(scoped)}`;
}

async function apiFetch<T>(request: HermesApiRequest): Promise<T> {
  const method = (request.method ?? (request.body || request.upload ? "POST" : "GET")).toUpperCase();
  const headers = new Headers();
  const token = sessionToken();
  if (token) headers.set(SESSION_HEADER, token);

  let body: BodyInit | undefined;
  if (request.upload) {
    const form = new FormData();
    form.append(
      "file",
      new Blob([request.upload.bytes], {
        type: request.upload.contentType || "application/octet-stream",
      }),
      request.upload.filename,
    );
    body = form;
  } else if (request.body !== undefined) {
    headers.set("Content-Type", "application/json");
    body = JSON.stringify(request.body);
  }

  const controller = new AbortController();
  const timeout = window.setTimeout(
    () => controller.abort(),
    request.timeoutMs ?? 60_000,
  );

  try {
    const res = await fetch(`${baseUrl()}${pathWithProfile(request.path, request.profile)}`, {
      method,
      headers,
      body,
      credentials: "include",
      signal: controller.signal,
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(`${res.status}: ${text || res.statusText}`);
    }
    if (res.status === 204) return undefined as T;
    const ct = res.headers.get("content-type") ?? "";
    if (ct.includes("application/json")) return (await res.json()) as T;
    return (await res.text()) as T;
  } finally {
    window.clearTimeout(timeout);
  }
}

function noopUnsub(): () => void {
  return () => {};
}

/** Matches Electron's default cap; the browser never reads local files anyway. */
const DATA_URL_READ_MAX = { defaultMaxMb: 10, maxBytes: 10 * 1024 * 1024, maxMb: 10 };

function unsupported(what: string): Error {
  return new Error(`${what} is not available in the browser dashboard.`);
}

/** Bootstrap never runs in the browser — the gateway is already up. */
function idleBootstrapState() {
  return {
    active: false,
    manifest: null,
    stages: {},
    error: null as string | null,
    log: [] as { ts: number; stage: string | null; line: string }[],
    startedAt: null as number | null,
    completedAt: null as number | null,
    setupChoice: null,
    unsupportedPlatform: null,
  };
}

/** Install `window.hermesDesktop` once. Idempotent. */
export function installHermesDesktopStub(): void {
  if (typeof window === "undefined") return;
  // Browser host for the desktop renderer (Hermes One is always-on in desktop).
  window.__HERMES_DESKTOP_EMBED__ = true;
  if (window.hermesDesktop && (window as { __HERMES_DESKTOP_STUB__?: boolean }).__HERMES_DESKTOP_STUB__) {
    return;
  }

  const stub = {
    getConnection: async (): Promise<HermesConnection> => {
      const wsUrl = await mintWsUrl();
      return {
        baseUrl: baseUrl(),
        isFullscreen: false,
        mode: "remote",
        authMode: window.__HERMES_AUTH_REQUIRED__ ? "oauth" : "token",
        nativeOverlayWidth: 0,
        source: "local",
        token: sessionToken(),
        wsUrl,
        logs: [],
        windowButtonPosition: null,
      };
    },
    revalidateConnection: async () => ({ ok: true, rebuilt: false }),
    touchBackend: async () => ({ ok: true }),
    getGatewayWsUrl: async (): Promise<GatewayWsUrlResult> => {
      try {
        return { ok: true, wsUrl: await mintWsUrl() };
      } catch (err) {
        return {
          ok: false,
          error: err instanceof Error ? err.message : String(err),
          needsOauthLogin: Boolean(window.__HERMES_AUTH_REQUIRED__),
        };
      }
    },
    openSessionWindow: async () => ({ ok: false, error: "unsupported-in-browser" }),
    openWindow: async () => ({ ok: false, error: "unsupported-in-browser" }),
    claimAmbientCue: async () => true,
    petOverlay: {
      open: async () => ({ ok: false }),
      close: async () => ({ ok: true }),
      setBounds: () => {},
      setIgnoreMouse: () => {},
      setFocusable: () => {},
      pushState: () => {},
      control: () => {},
      onState: () => noopUnsub(),
      onControl: () => noopUnsub(),
    },
    quickEntry: {
      getSettings: async () => ({ enabled: false, shortcut: "", registered: false }),
      setSettings: async () => ({ enabled: false, shortcut: "", registered: false }),
      submit: () => {},
      dismiss: () => {},
      pushState: () => {},
      onState: () => noopUnsub(),
      onSubmit: () => noopUnsub(),
      onShown: () => noopUnsub(),
    },
    getBootProgress: async () => emptyBootProgress(),
    onBootProgress: () => noopUnsub(),
    getConnectionConfig: async () => ({
      envOverride: false,
      mode: "local" as const,
      profile: null,
      remoteAuthMode: "token" as const,
      remoteOauthConnected: false,
      remoteTokenPreview: null,
      remoteTokenSet: false,
      remoteUrl: "",
      cloudOrg: "",
      sshHost: "",
      sshUser: "",
      sshPort: null,
      sshKeyPath: "",
      sshRemoteHermesPath: "",
      sshRemoteProfile: "",
    }),
    saveConnectionConfig: async (payload: unknown) => payload,
    applyConnectionConfig: async (payload: unknown) => payload,
    testConnectionConfig: async () => ({ ok: true }),
    sshConfigHosts: async () => ({ hosts: [] }),
    sshResolveHost: async () => ({ ok: false }),
    probeConnectionConfig: async () => ({ ok: false }),
    oauthLoginConnectionConfig: async () => ({ ok: false }),
    oauthLogoutConnectionConfig: async () => ({ ok: true }),
    cloud: {
      status: async () => ({ signedIn: false }),
      login: async () => ({ signedIn: false, ok: false }),
      logout: async () => ({ signedIn: false, ok: true }),
      discover: async () => ({ agents: [] }),
      agentSignIn: async () => ({ ok: false }),
    },
    profile: {
      get: async () => ({ profile: null }),
      set: async () => ({ profile: null }),
    },
    api: apiFetch,
    notify: async () => false,
    requestMicrophoneAccess: async () => {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        stream.getTracks().forEach((t) => t.stop());
        return true;
      } catch {
        return false;
      }
    },
    readFileDataUrl: async () => {
      throw new Error("Local file reads are not available in the browser dashboard.");
    },
    readFileText: async () => {
      throw new Error("Local file reads are not available in the browser dashboard.");
    },
    selectPaths: async () => [],
    writeClipboard: async (text: string) => {
      try {
        await navigator.clipboard.writeText(text);
        return true;
      } catch {
        return false;
      }
    },
    readClipboard: async () => {
      try {
        return await navigator.clipboard.readText();
      } catch {
        return "";
      }
    },
    saveImageFromUrl: async () => false,
    saveImageBuffer: async () => {
      throw new Error("Saving files is not available in the browser dashboard.");
    },
    saveClipboardImage: async () => {
      throw new Error("Clipboard image save is not available in the browser dashboard.");
    },
    getPathForFile: () => "",
    normalizePreviewTarget: async () => null,
    watchPreviewFile: async () => ({ id: "noop", path: "" }),
    stopPreviewFileWatch: async () => true,
    /** No fs watcher in the browser — nothing ever fires. */
    onPreviewFileChanged: () => noopUnsub(),
    dataUrlReadMax: {
      get: async () => DATA_URL_READ_MAX,
      set: async () => DATA_URL_READ_MAX,
    },
    setActiveWork: () => {},
    setTitleBarTheme: () => {},
    setNativeTheme: () => {},
    setTranslucency: () => {},
    setKeepAwake: () => {},
    setPreviewShortcutActive: () => {},
    zoom: {
      get: async () => ({ level: 0, percent: 100 }),
      setPercent: () => {},
      onChanged: () => noopUnsub(),
    },
    openExternal: async (url: string) => {
      window.open(url, "_blank", "noopener,noreferrer");
    },
    openPreviewInBrowser: async (url: string) => {
      window.open(url, "_blank", "noopener,noreferrer");
    },
    fetchLinkTitle: async () => "",
    sanitizeWorkspaceCwd: async (cwd?: string | null) => ({
      cwd: cwd || "",
      sanitized: false,
    }),
    settings: {
      getDefaultProjectDir: async () => ({
        defaultLabel: "Home",
        dir: null,
        resolvedCwd: "",
      }),
      pickDefaultProjectDir: async () => ({ canceled: true, dir: null }),
      setDefaultProjectDir: async () => ({ dir: null }),
    },
    revealLogs: async () => ({ ok: false, path: "" }),
    getRecentLogs: async () => ({ path: "", lines: [] }),
    readDir: async () => ({ entries: [] }),
    terminal: {
      cwd: async () => null,
      dispose: async () => true,
      resize: async () => false,
      start: async () => {
        throw unsupported("The local terminal");
      },
      write: async () => false,
      onData: () => noopUnsub(),
      onExit: () => noopUnsub(),
    },
    getBootstrapState: async () => idleBootstrapState(),
    continueBootstrapLocal: async () => ({ ok: false }),
    resetBootstrap: async () => ({ ok: false }),
    repairBootstrap: async () => ({ ok: false }),
    cancelBootstrap: async () => ({ ok: true, cancelled: false }),
    onBootstrapEvent: () => noopUnsub(),
    getVersion: async () => ({
      appVersion: "web",
      electronVersion: "",
      nodeVersion: "",
      platform: navigator.platform || "web",
      hermesRoot: "",
    }),
    getRemoteDisplayReason: async () => null,
    /** Updates are driven by whoever runs the gateway, not the browser tab. */
    updates: {
      check: async () => ({ supported: false, reason: "browser-dashboard" }),
      apply: async () => ({ ok: false, error: "unsupported-in-browser" }),
      getBranch: async () => ({ branch: "" }),
      setBranch: async () => ({ branch: "" }),
      onProgress: () => noopUnsub(),
    },
    uninstall: {
      summary: async () => {
        throw unsupported("Uninstall");
      },
      run: async () => ({ ok: false, error: "unsupported-in-browser" }),
    },
    themes: {
      fetchMarketplace: async () => {
        throw unsupported("Marketplace theme download");
      },
      searchMarketplace: async () => [],
    },
    /** Cmd+F needs Electron's webContents.findInPage — browsers keep their own. */
    findInPage: async () => ({ count: 0 }),
    stopFindInPage: async () => {},
    onFoundInPage: () => noopUnsub(),
    getOnBattery: async () => false,
    onBatteryChanged: () => noopUnsub(),
    onPowerResume: () => noopUnsub(),
    onConnectionApplied: () => noopUnsub(),
    onBackendExit: () => noopUnsub(),
    onWindowStateChanged: () => noopUnsub(),
    onClosePreviewRequested: () => noopUnsub(),
    onOpenFolderRequested: () => noopUnsub(),
    onOpenUpdatesRequested: () => noopUnsub(),
    onFocusSession: () => noopUnsub(),
    onNotificationAction: () => noopUnsub(),
    onDeepLink: () => noopUnsub(),
    signalDeepLinkReady: async () => ({ ok: true }),
  };

  // Desktop types are richer; cast once at the boundary.
  window.hermesDesktop = stub as unknown as Window["hermesDesktop"];
  (window as { __HERMES_DESKTOP_STUB__?: boolean }).__HERMES_DESKTOP_STUB__ = true;
}

declare global {
  // Desktop's full typed bridge; browser stub is structurally compatible.
  interface Window {
    /** True when the desktop renderer is hosted inside the dashboard browser. */
    __HERMES_DESKTOP_EMBED__?: boolean;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    hermesDesktop: any;
  }
}
