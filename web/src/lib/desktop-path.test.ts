import { describe, expect, it } from "vitest";

import {
  dashboardPathToDesktop,
  isDesktopEmbedPath,
  isDesktopOverlayRoute,
} from "./desktop-path";

describe("dashboardPathToDesktop", () => {
  it("maps chat to the desktop root", () => {
    expect(dashboardPathToDesktop("/chat")).toBe("/");
  });

  it("maps the desktop-backed surfaces one to one", () => {
    expect(dashboardPathToDesktop("/cron")).toBe("/cron");
    expect(dashboardPathToDesktop("/profiles")).toBe("/profiles");
    expect(dashboardPathToDesktop("/webhooks")).toBe("/webhooks");
    expect(dashboardPathToDesktop("/artifacts")).toBe("/artifacts");
    expect(dashboardPathToDesktop("/agents")).toBe("/agents");
    expect(dashboardPathToDesktop("/starmap")).toBe("/starmap");
    expect(dashboardPathToDesktop("/command-center")).toBe("/command-center");
    expect(dashboardPathToDesktop("/clients")).toBe("/clients");
    expect(dashboardPathToDesktop("/campaigns")).toBe("/campaigns");
  });

  it("renames the surfaces the desktop app labels differently", () => {
    expect(dashboardPathToDesktop("/channels")).toBe("/messaging");
    expect(dashboardPathToDesktop("/config")).toBe("/settings");
    expect(dashboardPathToDesktop("/env")).toBe("/settings?tab=keys");
    expect(dashboardPathToDesktop("/profiles/new")).toBe("/profiles");
  });

  it("ignores a trailing slash", () => {
    expect(dashboardPathToDesktop("/cron/")).toBe("/cron");
  });

  it("falls back to the chat root for dashboard-only pages", () => {
    expect(dashboardPathToDesktop("/mcp")).toBe("/");
    expect(dashboardPathToDesktop("/nope")).toBe("/");
  });
});

describe("isDesktopEmbedPath", () => {
  it("claims every desktop-backed route", () => {
    for (const path of [
      "/chat",
      "/skills",
      "/settings",
      "/env",
      "/cron",
      "/channels",
      "/webhooks",
      "/profiles",
      "/artifacts",
      "/agents",
      "/starmap",
      "/command-center",
      "/clients",
      "/campaigns",
    ]) {
      expect(isDesktopEmbedPath(path)).toBe(true);
    }
  });

  it("leaves dashboard-only pages alone", () => {
    for (const path of ["/mcp", "/files", "/pairing", "/docs", "/logs", "/"]) {
      expect(isDesktopEmbedPath(path)).toBe(false);
    }
  });
});

describe("isDesktopOverlayRoute", () => {
  it("recognises overlay views, query included", () => {
    expect(isDesktopOverlayRoute("/settings")).toBe(true);
    expect(isDesktopOverlayRoute("/settings?tab=keys")).toBe(true);
    expect(isDesktopOverlayRoute("/cron")).toBe(true);
    expect(isDesktopOverlayRoute("/command-center")).toBe(true);
  });

  it("excludes workspace pages and the chat", () => {
    expect(isDesktopOverlayRoute("/")).toBe(false);
    expect(isDesktopOverlayRoute("/skills")).toBe(false);
    expect(isDesktopOverlayRoute("/messaging")).toBe(false);
    expect(isDesktopOverlayRoute("/artifacts")).toBe(false);
  });
});
