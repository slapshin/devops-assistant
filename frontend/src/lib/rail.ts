import { ref, watch } from "vue";

const RAIL_STORAGE_KEY = "assistant.rail";

function readHidden(): boolean {
  try {
    return localStorage.getItem(RAIL_STORAGE_KEY) === "hidden";
  } catch {
    return false;
  }
}

function persistHidden(hidden: boolean) {
  try {
    if (hidden) localStorage.setItem(RAIL_STORAGE_KEY, "hidden");
    else localStorage.removeItem(RAIL_STORAGE_KEY);
  } catch {
    /* storage unavailable: keep in memory */
  }
}

/** Whether the left navigation rail is hidden; a per-viewer convenience kept in localStorage. */
export const railHidden = ref<boolean>(readHidden());

watch(railHidden, persistHidden);
