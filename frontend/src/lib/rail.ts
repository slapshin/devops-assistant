import { ref, watch } from "vue";

const RAIL_STORAGE_KEY = "assistant.rail";

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(RAIL_STORAGE_KEY) === "collapsed";
  } catch {
    return false;
  }
}

function persistCollapsed(collapsed: boolean) {
  try {
    if (collapsed) localStorage.setItem(RAIL_STORAGE_KEY, "collapsed");
    else localStorage.removeItem(RAIL_STORAGE_KEY);
  } catch {
    /* storage unavailable: keep in memory */
  }
}

/** Whether the left navigation shows icons only; a per-viewer convenience kept in localStorage. */
export const railCollapsed = ref<boolean>(readCollapsed());

watch(railCollapsed, persistCollapsed);
