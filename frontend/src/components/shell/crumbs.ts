import type { RouteLocationRaw } from "vue-router";

/** One breadcrumb; the last one is the current page and is never a link. */
export interface Crumb {
  label: string;
  to?: RouteLocationRaw;
  mono?: boolean;
}
