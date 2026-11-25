// Light / Dark / System switch controller from the Green & Mint theme kit.
// The pre-paint script in index.html sets data-theme before first render; this
// keeps it in step with the analyst's choice, the OS setting and other tabs.

// "system" is never offered in the UI: it is the default until the analyst picks
// Light or Dark, and simply means "follow the OS".
export type Theme = "light" | "dark";
export type ThemePref = Theme | "system";

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

/** The theme actually on screen, whether chosen or inherited from the OS. */
export function getAppliedTheme(): Theme {
  return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
}

function apply(pref: ThemePref) {
  const root = document.documentElement;
  if (pref === "system" && !media && root.hasAttribute("data-theme")) return; // can't read the OS: keep what's set
  const theme = pref === "system" ? (media && !media.matches ? "dark" : "light") : pref;
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
    if (getPreference() === "system") {
      apply("system");
      onChange?.();
    }
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
