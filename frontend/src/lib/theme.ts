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
  font: string;
  bg: string;
  text: string;
  muted: string;
  border: string;
  grid: string;
  observed: string;
  expected: string;
  band: string;
  /** Text-strength severity colours: episode tints (at low opacity) and heuristic lines. */
  severity: Record<string, string>;
}

const CHART_FONT = '"IBM Plex Sans", system-ui, sans-serif';
const LIGHT_PALETTE: ChartPalette = {
  font: CHART_FONT, bg: "#ffffff", text: "#24292e", muted: "#5d6168", border: "#c7cad0", grid: "#eceef1",
  observed: "#1f62e0", expected: "#1f62e0", band: "rgba(31, 98, 224, 0.12)",
  severity: { critical: "#c4162a", high: "#b84c00", medium: "#8a6d00", low: "#5d6168" },
};
const DARK_PALETTE: ChartPalette = {
  font: CHART_FONT, bg: "#181b1f", text: "#ccccdc", muted: "#8e8e9e", border: "#3a3e45", grid: "#23262c",
  observed: "#5794f2", expected: "#5794f2", band: "rgba(87, 148, 242, 0.12)",
  severity: { critical: "#ff5c6f", high: "#ff9830", medium: "#fade2a", low: "#8e8e9e" },
};

/** ECharts cannot read CSS variables, so charts take the palette of the active theme (mirrors tokens.css). */
export const chartPalette = computed<ChartPalette>(() => (isDark.value ? DARK_PALETTE : LIGHT_PALETTE));
