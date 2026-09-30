import { createRouter, createWebHistory, type RouteRecordRaw, type RouterHistory } from "vue-router";
import NotImplemented from "./components/NotImplemented.vue";
import StartView from "./views/StartView.vue";

const pending = (view: string, task = "T008") => ({
  component: NotImplemented,
  props: { view, task },
});

/** Routes frozen by docs/UI_SPEC.md §1; views other than Start arrive in T008. */
export const routes: RouteRecordRaw[] = [
  { path: "/", name: "start", component: StartView },
  { path: "/analyses/:id", name: "job", ...pending("Job progress") },
  { path: "/reports/:id", name: "overview", ...pending("Overview") },
  { path: "/reports/:id/findings", name: "findings", ...pending("Findings") },
  { path: "/reports/:id/findings/:findingId", name: "evidence", ...pending("Evidence") },
  { path: "/reports/:id/trends", name: "trends", ...pending("Trends") },
  { path: "/:pathMatch(.*)*", name: "not-found", ...pending("Page not found", "—") },
];

export function makeRouter(history: RouterHistory = createWebHistory()) {
  return createRouter({ history, routes });
}
