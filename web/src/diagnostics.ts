/**
 * Crash diagnostics for the web SPA.
 *
 * «Maximum update depth exceeded / getSnapshot should be cached» in the
 * minified vendor chunk names no component — these hooks print the REAL
 * culprit (component stack + error) to the console and keep a rolling
 * breadcrumb in sessionStorage so the last crash survives the reload.
 *
 * Read the last crash in DevTools:  JSON.parse(sessionStorage.hermesLastCrash)
 */

const MAX_STACK_CHARS = 4000;

function record(kind: string, error: unknown, componentStack?: string | null) {
  const entry = {
    kind,
    at: new Date().toISOString(),
    href: window.location.href,
    message: error instanceof Error ? error.message : String(error),
    stack: error instanceof Error ? String(error.stack || "").slice(0, MAX_STACK_CHARS) : "",
    componentStack: String(componentStack || "").slice(0, MAX_STACK_CHARS),
  };
  // eslint-disable-next-line no-console
  console.error(`[hermes-crash] ${kind}: ${entry.message}`, entry.componentStack || "(no component stack)");
  try {
    sessionStorage.setItem("hermesLastCrash", JSON.stringify(entry));
  } catch {
    /* storage may be unavailable — the console line above still lands */
  }
}

export function installGlobalDiagnostics() {
  window.addEventListener("error", (event) => {
    record("window.onerror", event.error ?? event.message);
  });
  window.addEventListener("unhandledrejection", (event) => {
    record("unhandledrejection", event.reason);
  });
}

/** React 19 root options: log uncaught/caught render errors WITH the
 * component stack — this is the line that names the looping component. */
export const rootErrorOptions = {
  onUncaughtError(error: unknown, errorInfo: { componentStack?: string }) {
    record("react.uncaught", error, errorInfo.componentStack);
  },
  onCaughtError(error: unknown, errorInfo: { componentStack?: string }) {
    record("react.caught", error, errorInfo.componentStack);
  },
  onRecoverableError(error: unknown, errorInfo: { componentStack?: string }) {
    record("react.recoverable", error, errorInfo.componentStack);
  },
};
