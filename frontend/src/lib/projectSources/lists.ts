/** Lower-cased, unique, sorted items of a comma- or space-separated list. */
export function parseList(raw: string): string[] {
  const items = raw
    .split(/[\s,]+/)
    .map((h) => h.trim().toLowerCase())
    .filter(Boolean);
  return [...new Set(items)].sort();
}

/** Unique, sorted items of a comma- or space-separated list, case kept (Wazuh agent names). */
export function parseNames(raw: string): string[] {
  return [...new Set(raw.split(/[\s,]+/).filter(Boolean))].sort();
}

/** Unique, sorted items of a comma- or newline-separated list, case and inner spaces kept (Grafana folders). */
export function parseFolders(raw: string): string[] {
  return [...new Set(raw.split(/[,\n]+/).map((f) => f.trim()).filter(Boolean))].sort();
}

export const parseHostnames = (raw: string) => parseList(raw.replace(/\.(?=[\s,]|$)/g, ""));
