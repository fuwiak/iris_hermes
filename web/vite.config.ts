import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";
import fs from "fs";

const BACKEND = process.env.HERMES_DASHBOARD_URL ?? "http://127.0.0.1:9119";
const DESKTOP_SRC = path.resolve(__dirname, "../apps/desktop/src");
const WEB_SRC = path.resolve(__dirname, "./src");
const EMPTY_SHIM = path.resolve(__dirname, "./src/shims/empty-module.ts");

/**
 * Desktop (and shared) sources live outside `web/` and are pulled into the
 * dashboard bundle via `@desktop` / `@hermes/shared`. Their bare imports walk
 * `node_modules` from the importer path, which misses `web/node_modules` in
 * Docker/railway (`cd web && npm install`). Re-resolve every bare package
 * import from those trees as if the importer were the web package so we do
 * not play allowlist whack-a-mole on each new desktop dep.
 */
function resolveOutsideWebDepsFromWeb(): Plugin {
  const webImporter = path.resolve(__dirname, "package.json");
  const webRoot = path.resolve(__dirname) + path.sep;
  const outsideMarkers = [
    `${path.sep}apps${path.sep}desktop${path.sep}`,
    `${path.sep}apps${path.sep}shared${path.sep}`,
  ];
  return {
    name: "hermes:resolve-outside-web-deps-from-web",
    enforce: "pre",
    async resolveId(source, importer, options) {
      if (!importer || !source) return null;
      // Relative, absolute, virtual, and our own aliases — leave alone.
      if (
        source.startsWith(".") ||
        source.startsWith("/") ||
        source.startsWith("\0") ||
        source.startsWith("@/") ||
        source.startsWith("@desktop") ||
        source.startsWith("@hermes/shared") ||
        source.startsWith("node:")
      ) {
        return null;
      }
      // Node-only shims are handled by resolve.alias.
      if (source === "electron" || source === "node-pty" || source === "simple-git") {
        return null;
      }
      if (importer.startsWith(webRoot)) return null;
      const fromOutside = outsideMarkers.some((m) => importer.includes(m));
      if (!fromOutside) return null;
      return this.resolve(source, webImporter, { ...options, skipSelf: true });
    },
  };
}

/**
 * Desktop sources import `@/…` meaning `apps/desktop/src`. Web uses `@` for
 * `web/src`. Route `@/` by importer path so both graphs resolve correctly.
 */
function desktopAtAlias(): Plugin {
  const debugNoop = path.resolve(DESKTOP_SRC, "debug/dev-only.noop.ts");
  return {
    name: "hermes:desktop-at-alias",
    enforce: "pre",
    async resolveId(source, importer, options) {
      if (!source.startsWith("@/")) return null;
      if (!importer) return null;
      const fromDesktop =
        importer.includes(`${path.sep}apps${path.sep}desktop${path.sep}`) ||
        importer.startsWith(DESKTOP_SRC);
      const targetRoot = fromDesktop ? DESKTOP_SRC : WEB_SRC;
      const rel = source.slice(2);
      if (fromDesktop && (rel === "debug/dev-only" || rel.startsWith("debug/dev-only."))) {
        return debugNoop;
      }
      const abs = path.resolve(targetRoot, rel);
      // Let Vite finish extension resolution (.ts/.tsx/…).
      return this.resolve(abs, importer, { ...options, skipSelf: true });
    },
  };
}

/**
 * In production the Python `hermes dashboard` server injects a one-shot
 * session token into `index.html` (see `hermes_cli/web_server.py`). The
 * Vite dev server serves its own `index.html`, so unless we forward that
 * token, every protected `/api/*` call 401s.
 *
 * This plugin fetches the running dashboard's `index.html` on each dev page
 * load, scrapes the `window.__HERMES_SESSION_TOKEN__` assignment, and
 * re-injects it into the dev HTML. No-op in production builds.
 */
function hermesDevToken(): Plugin {
  const TOKEN_RE = /window\.__HERMES_SESSION_TOKEN__\s*=\s*"([^"]+)"/;
  const EMBEDDED_RE =
    /window\.__HERMES_DASHBOARD_EMBEDDED_CHAT__\s*=\s*(true|false)/;

  return {
    name: "hermes:dev-session-token",
    apply: "serve",
    async transformIndexHtml() {
      try {
        const res = await fetch(BACKEND, { headers: { accept: "text/html" } });
        const html = await res.text();
        const match = html.match(TOKEN_RE);
        if (!match) {
          console.warn(
            `[hermes] Could not find session token in ${BACKEND} — ` +
              `is \`hermes dashboard\` running? /api calls will 401.`,
          );
          return;
        }
        const embeddedMatch = html.match(EMBEDDED_RE);
        const embeddedJs = embeddedMatch ? embeddedMatch[1] : "true";
        return [
          {
            tag: "script",
            injectTo: "head",
            children:
              `window.__HERMES_SESSION_TOKEN__="${match[1]}";` +
              `window.__HERMES_DASHBOARD_EMBEDDED_CHAT__=${embeddedJs};`,
          },
        ];
      } catch (err) {
        console.warn(
          `[hermes] Dashboard at ${BACKEND} unreachable — ` +
            `start it with \`hermes dashboard\` or set HERMES_DASHBOARD_URL. ` +
            `(${(err as Error).message})`,
        );
      }
    },
  };
}

export default defineConfig({
  plugins: [
    react(),
    tailwindcss(),
    hermesDevToken(),
    desktopAtAlias(),
    resolveOutsideWebDepsFromWeb(),
  ],
  resolve: {
    alias: [
      {
        find: "@desktop",
        replacement: DESKTOP_SRC,
      },
      {
        find: "@hermes/shared",
        replacement: path.resolve(__dirname, "../apps/shared/src"),
      },
      // Node-only deps that must never reach the browser bundle.
      { find: "node-pty", replacement: EMPTY_SHIM },
      { find: "simple-git", replacement: EMPTY_SHIM },
      { find: "electron", replacement: EMPTY_SHIM },
    ],
    // TS BEFORE JS — the desktop tree we alias into (`@desktop`, `@/…` from
    // apps/desktop/src) collects gitignored `foo.js` artifacts from
    // `tsc --build`. Vite's default order resolves those ahead of `foo.tsx`,
    // so the embed would paint stale desktop code. Mirror of the same setting
    // in apps/desktop/vite.config.ts.
    extensions: [".tsx", ".ts", ".mts", ".jsx", ".mjs", ".js", ".json"],
    // When @nous-research/ui is symlinked via `file:../../design-language`,
    // Node's module resolution would pick up shared deps from
    // design-language/node_modules/*, giving us two copies + breaking
    // hooks (useRef-of-null), webgl contexts, etc. Force everything that
    // exists in BOTH places to use the dashboard's copy.
    //
    // Don't list packages here that only exist in the DS (nanostores,
    // @nanostores/react) — Vite dedupe errors out when it can't find
    // them at the project root.
    dedupe: [
      "react",
      "react-dom",
      "react-router",
      "@react-three/fiber",
      "@observablehq/plot",
      "three",
      "leva",
      "gsap",
      "nanostores",
      "@nanostores/react",
      "@tanstack/react-query",
      "@assistant-ui/react",
      "@nous-research/ui",
      "web-haptics",
      "radix-ui",
      "@tabler/icons-react",
    ],
  },
  optimizeDeps: {
    exclude: ["node-pty", "electron", "simple-git"],
  },
  build: {
    outDir: "../hermes_cli/web_dist",
    emptyOutDir: true,
    // Shell stays a bit over Vite's 500 kB default after vendor splits;
    // page/xterm chunks load on demand. Keep a modest ceiling so a true
    // regression still warns.
    chunkSizeWarningLimit: 2500,
    // Split heavy vendors so the first dashboard paint does not download
    // xterm/three/plot/etc. until a route actually needs them. Lazy page
    // imports in App.tsx create the route boundaries; these groups keep
    // shared node_modules out of every page chunk.
    rolldownOptions: {
      output: {
        codeSplitting: {
          minSize: 20_000,
          groups: [
            {
              name: "react-vendor",
              test: /node_modules[\\/](react|react-dom|scheduler|react-router|react-router)([\\/]|$)/,
            },
            {
              name: "xterm",
              test: /node_modules[\\/]@xterm[\\/]/,
            },
            {
              name: "three",
              test: /node_modules[\\/](three|@react-three)([\\/]|$)/,
            },
            {
              name: "plot",
              test: /node_modules[\\/]@observablehq[\\/]plot([\\/]|$)/,
            },
            {
              name: "motion",
              test: /node_modules[\\/](motion|framer-motion)([\\/]|$)/,
            },
            {
              name: "ui",
              test: /node_modules[\\/]@nous-research[\\/]ui([\\/]|$)/,
            },
            {
              name: "echarts",
              test: /node_modules[\\/]echarts([\\/]|$)/,
            },
            {
              name: "plotly",
              test: /node_modules[\\/]plotly\.js-dist-min([\\/]|$)/,
            },
            {
              name: "vendor",
              test: /node_modules[\\/]/,
            },
          ],
        },
      },
    },
  },
  server: {
    fs: {
      allow: [
        path.resolve(__dirname, ".."),
        ...[
          path.resolve(__dirname, "node_modules"),
          path.resolve(__dirname, "../node_modules"),
        ].flatMap((p) => {
          try {
            return [fs.realpathSync(p)];
          } catch {
            return [];
          }
        }),
      ],
    },
    proxy: {
      "/api": {
        target: BACKEND,
        ws: true,
      },
      // Same host as `hermes dashboard` must serve these; Vite has no
      // dashboard-plugins/* files, so without this, plugin scripts 404
      // or receive index.html in dev.
      "/dashboard-plugins": BACKEND,
    },
  },
});
