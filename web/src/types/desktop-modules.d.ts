/**
 * Ambient module shims so `tsc -b` does not typecheck the whole desktop tree.
 * Vite resolves these to real files via aliases in vite.config.ts.
 */

declare module "@desktop/app" {
  import type { ComponentType } from "react";
  const App: ComponentType;
  export default App;
}

declare module "@desktop/i18n" {
  import type { ComponentType, ReactNode } from "react";
  export const I18nProvider: ComponentType<{ children: ReactNode }>;
}

declare module "@desktop/themes/context" {
  import type { ComponentType, ReactNode } from "react";
  export const ThemeProvider: ComponentType<{ children: ReactNode }>;
}

declare module "@desktop/components/haptics-provider" {
  import type { ComponentType, ReactNode } from "react";
  export const HapticsProvider: ComponentType<{ children: ReactNode }>;
}

declare module "@desktop/components/ui/tooltip" {
  import type { ComponentType, ReactNode } from "react";
  export const RootTooltipProvider: ComponentType<{ children: ReactNode }>;
}

declare module "@desktop/app/settings" {
  import type { ComponentType } from "react";
  export const SettingsView: ComponentType<{
    onClose: () => void;
    gateway?: unknown;
    onConfigSaved?: () => void;
    onMainModelChanged?: (provider: string, model: string) => void;
  }>;
}

declare module "@desktop/styles.css" {}

declare module "@desktop/*" {
  const mod: unknown;
  export = mod;
}
