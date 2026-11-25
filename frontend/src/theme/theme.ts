// Light / Dark / System switch controller from the Green & Mint theme kit.
// The pre-paint script in index.html sets data-theme before first render; this
// keeps it in step with the analyst's choice, the OS setting and other tabs.

export type ThemePref = "light" | "dark" | "system";

const KEY = "ds-theme";
// jsdom (the test environment) has no matchMedia; treat that as "OS is light".
const media: MediaQueryList | null =
  typeof matchMedia === "function" ? matchMedia("(prefers-color-scheme: light)") : null;

export function getPreference(): ThemePref {
  try {
    const v = localStorage.getItem(KEY);
    if (v === "light" || v === "dark") return v;
  } catch {
    /* storage blocked */
  }
  return "system";
}

function apply(pref: ThemePref) {
  const theme = pref === "system" ? (media && !media.matches ? "dark" : "light") : pref;
  const root = document.documentElement;
  if (root.getAttribute("data-theme") === theme) return;
  root.classList.add("ds-theme-switching"); // swap every component at once
  root.setAttribute("data-theme", theme);
  requestAnimationFrame(() =>
    requestAnimationFrame(() => root.classList.remove("ds-theme-switching")),
  );
}

export function setPreference(pref: ThemePref) {
  try {
    if (pref === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, pref);
  } catch {
    /* storage blocked */
  }
  apply(pref);
}

/** Applies the stored preference and follows OS and other-tab changes. Returns a cleanup. */
export function initTheme(onChange?: () => void): () => void {
  apply(getPreference());
  const onMedia = () => {
    if (getPreference() === "system") apply("system");
  };
  const onStorage = (e: StorageEvent) => {
    if (e.key === KEY) {
      apply(getPreference());
      onChange?.();
    }
  };
  media?.addEventListener("change", onMedia);
  addEventListener("storage", onStorage);
  return () => {
    media?.removeEventListener("change", onMedia);
    removeEventListener("storage", onStorage);
  };
}
