import { useCallback, useEffect, useState } from "react";
import {
  NAV_MODE_CHANGE_EVENT,
  NAV_MODE_STORAGE_KEY,
  type NavMode,
  readNavMode,
  writeNavMode,
} from "@hermes/shared";

/** Persist standard/pro left-nav mode (localStorage + same-window sync). */
export function useNavMode(): [NavMode, (mode: NavMode) => void] {
  const [mode, setModeState] = useState<NavMode>(() => readNavMode());

  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key !== null && e.key !== NAV_MODE_STORAGE_KEY) return;
      setModeState(readNavMode());
    };
    const onCustom = (e: Event) => {
      const detail = (e as CustomEvent<NavMode>).detail;
      if (detail === "pro" || detail === "standard") setModeState(detail);
      else setModeState(readNavMode());
    };
    window.addEventListener("storage", onStorage);
    window.addEventListener(NAV_MODE_CHANGE_EVENT, onCustom);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener(NAV_MODE_CHANGE_EVENT, onCustom);
    };
  }, []);

  const setMode = useCallback((next: NavMode) => {
    writeNavMode(next);
    setModeState(next);
  }, []);

  return [mode, setMode];
}
