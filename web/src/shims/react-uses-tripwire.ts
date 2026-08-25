/**
 * React re-export with a `useSyncExternalStore` loop tripwire.
 *
 * Prod crashes with «Maximum update depth exceeded. The result of getSnapshot
 * should be cached…» thrown from the minified vendor chunk — React raises it
 * from the work loop, so root error hooks never see a component stack and the
 * culprit hook stays anonymous. The web build aliases bare `react` imports to
 * this module (see hermes:react-uses-tripwire in vite.config.ts), which wraps
 * getSnapshot: when one hook's snapshot identity flips >50 times within a
 * second, it logs `[uses-loop]` ONCE with the getSnapshot source code and the
 * JS call stack — enough to name the hook even in a minified bundle.
 *
 * Remove after the loop is found and fixed.
 */
// @ts-nocheck — @types/react uses `export =`, which TS refuses to `export *`
// from; at runtime (ESM via vite) the star re-export is exactly right.
// eslint-disable-next-line no-restricted-imports
import * as ReactActual from "react";

export * from "react";

const REAL_USES = ReactActual.useSyncExternalStore;

interface Track {
  last: unknown;
  flips: number;
  windowStart: number;
  reported: boolean;
}

export function useSyncExternalStore<T>(
  subscribe: (onStoreChange: () => void) => () => void,
  getSnapshot: () => T,
  getServerSnapshot?: () => T,
): T {
  // Per HOOK INSTANCE via useRef — keying by getSnapshot source or by the
  // subscribe fn both aggregate unrelated hooks (minified nanostores bodies
  // are identical; assistant-ui shares one bound subscribe) and cry wolf.
  const trackRef = ReactActual.useRef<Track | null>(null);

  const wrapped = (): T => {
    const value = getSnapshot();
    const now = Date.now();
    const track = trackRef.current;

    if (!track || now - track.windowStart > 1000) {
      trackRef.current = {
        last: value,
        flips: 0,
        windowStart: now,
        reported: track?.reported ?? false,
      };
      return value;
    }

    if (!Object.is(track.last, value)) {
      track.last = value;
      track.flips += 1;

      if (track.flips > 100 && !track.reported) {
        track.reported = true;
        let preview = "";
        try {
          preview = JSON.stringify(value)?.slice(0, 300) ?? String(value);
        } catch {
          preview = Object.prototype.toString.call(value);
        }
        const report = {
          at: new Date().toISOString(),
          getSnapshot: String(getSnapshot).slice(0, 400),
          subscribe: String(subscribe).slice(0, 400),
          preview,
          stack: String(new Error("uses-loop").stack).slice(0, 3000),
        };
        // eslint-disable-next-line no-console
        console.error("[uses-loop] ONE hook instance flips >100x/s — the infinite loop:", JSON.stringify(report));
        try {
          sessionStorage.setItem("hermesUsesLoop", JSON.stringify(report));
        } catch {
          /* console line above is the primary channel */
        }
      }
    }

    return value;
  };

  return REAL_USES(subscribe, wrapped, getServerSnapshot ?? wrapped);
}

// Default import consumers call React.useSyncExternalStore as a PROPERTY —
// a plain namespace default would hand them the unwrapped original and the
// tripwire would stay silent (exactly what happened on prod).
const patched = { ...ReactActual, useSyncExternalStore };
export default patched;
