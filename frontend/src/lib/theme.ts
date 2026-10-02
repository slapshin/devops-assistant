import { computed, ref, watch } from "vue";

export type ThemeChoice = "auto" | "light" | "dark";
export const THEME_CHOICES: { value: ThemeChoice; label: string }[] = [
  { value: "auto", label: "Auto" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

const THEME_STORAGE_KEY = "assistant.theme";
const DARK_SCHEME_QUERY = "(prefers-color-scheme: dark)";

function readChoice(): ThemeChoice {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY);
    return stored === "light" || stored === "dark" ? stored : "auto";
  } catch {
    return "auto";
  }
}

function applyChoice(choice: ThemeChoice) {
  const root = document.documentElement;
  if (choice === "auto") delete root.dataset.theme;
  else root.dataset.theme = choice;
}

function persistChoice(choice: ThemeChoice) {
  try {
    if (choice === "auto") localStorage.removeItem(THEME_STORAGE_KEY);
    else localStorage.setItem(THEME_STORAGE_KEY, choice);
  } catch {
    /* storage unavailable: keep in memory */
  }
}

/** Auto follows the OS; an explicit choice is a per-viewer convenience kept in localStorage. */
export const theme = ref<ThemeChoice>(readChoice());

const darkSchemeMedia = typeof window !== "undefined" && window.matchMedia ? window.matchMedia(DARK_SCHEME_QUERY) : null;
const systemDark = ref(darkSchemeMedia?.matches ?? false);
darkSchemeMedia?.addEventListener?.("change", (event) => (systemDark.value = event.matches));

export const isDark = computed(() => theme.value === "dark" || (theme.value === "auto" && systemDark.value));

watch(
  theme,
  (choice) => {
    applyChoice(choice);
    persistChoice(choice);
  },
  { immediate: true },
);

export interface ChartPalette {
  bg: string;
  text: string;
  muted: string;
  border: string;
  observed: string;
  expected: string;
  band: string;
  episode: string;
  threshold: string;
}

const LIGHT_PALETTE: ChartPalette = {
  bg: "#ffffff", text: "#1b1f24", muted: "#57606a", border: "#d0d7de", observed: "#1b1f24", expected: "#0550ae",
  band: "#ddf4ff", episode: "#ffebe9", threshold: "#a40e26",
};
const DARK_PALETTE: ChartPalette = {
  bg: "#161b22", text: "#e6edf3", muted: "#9198a1", border: "#30363d", observed: "#e6edf3", expected: "#79c0ff",
  band: "#132339", episode: "#3b1a1d", threshold: "#ff7b72",
};

/** ECharts cannot read CSS variables, so charts take the palette of the active theme (mirrors tokens.css). */
export const chartPalette = computed<ChartPalette>(() => (isDark.value ? DARK_PALETTE : LIGHT_PALETTE));
