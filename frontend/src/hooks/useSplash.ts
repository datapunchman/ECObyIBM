/**
 * useSplash — session-scoped gate for the ECO startup experience.
 *
 * The splash plays once per browser session (sessionStorage). Refreshing
 * the tab within the same session skips straight to the app; a new tab or
 * a restarted browser replays it.
 */
import { useCallback, useState } from "react";

const STORAGE_KEY = "eco:splash:shown";

function hasPlayed(): boolean {
  try {
    return sessionStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false; // storage unavailable (private mode) — play the splash
  }
}

export function useSplash(): {
  showSplash: boolean;
  completeSplash: () => void;
} {
  const [showSplash, setShowSplash] = useState<boolean>(() => !hasPlayed());

  const completeSplash = useCallback(() => {
    try {
      sessionStorage.setItem(STORAGE_KEY, "1");
    } catch {
      /* non-fatal */
    }
    setShowSplash(false);
  }, []);

  return { showSplash, completeSplash };
}

export default useSplash;
