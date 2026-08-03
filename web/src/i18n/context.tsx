import { createContext, useContext, useState, useCallback, useEffect, type ReactNode } from "react";
import type { Locale, Translations } from "./types";
import { en } from "./en";
import { ru } from "./ru";

const TRANSLATIONS: Record<Locale, Translations> = {
  en,
  ru,
};

// Locales whose script flows right-to-left. Consumed by the provider to set the
// document direction so Tailwind's logical utilities (ms-/me-, ps-/pe-) flip.
const RTL_LOCALES = new Set<Locale>();

// Display metadata for the language picker — endonym (native name) so users
// recognize their language even if they don't speak the current UI language.
// Exposed as a constant so the LanguageSwitcher and any future settings page
// can share the same list.
//
// We intentionally do NOT pair locales with country flags. Languages are not
// countries (English ≠ GB, Portuguese ≠ PT, Spanish ≠ ES, Chinese variants ≠
// any single jurisdiction). Endonyms are unambiguous and avoid the political
// mismapping that flag pairings inevitably create.
export const LOCALE_META: Record<Locale, { name: string }> = {
  en: { name: "English" },
  ru: { name: "Русский" },
};

const SUPPORTED_LOCALES = Object.keys(TRANSLATIONS) as Locale[];
const STORAGE_KEY = "hermes-locale";

function isLocale(value: string): value is Locale {
  return (SUPPORTED_LOCALES as string[]).includes(value);
}

function getInitialLocale(): Locale {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored && isLocale(stored)) return stored;
  } catch {
    // SSR or privacy mode
  }
  return "ru";
}

interface I18nContextValue {
  locale: Locale;
  setLocale: (l: Locale) => void;
  t: Translations;
}

const I18nContext = createContext<I18nContextValue>({
  locale: "ru",
  setLocale: () => {},
  t: ru,
});

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(getInitialLocale);

  const setLocale = useCallback((l: Locale) => {
    setLocaleState(l);
    try {
      localStorage.setItem(STORAGE_KEY, l);
    } catch {
      // ignore
    }
  }, []);

  useEffect(() => {
    if (typeof document === "undefined") return;
    document.documentElement.lang = locale;
    document.documentElement.dir = RTL_LOCALES.has(locale) ? "rtl" : "ltr";
  }, [locale]);

  const value: I18nContextValue = {
    locale,
    setLocale,
    t: TRANSLATIONS[locale],
  };

  return (
    <I18nContext.Provider value={value}>
      {children}
    </I18nContext.Provider>
  );
}

export function useI18n() {
  return useContext(I18nContext);
}
