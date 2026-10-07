import { createRouter, createWebHistory, type RouteRecordRaw, type RouterHistory } from "vue-router";
import NotImplemented from "./components/NotImplemented.vue";
import JobView from "./views/JobView.vue";
import FindingsView from "./views/report/FindingsView.vue";
import OverviewView from "./views/report/OverviewView.vue";
import ReportLayout from "./views/report/ReportLayout.vue";
import TimelineView from "./views/report/TimelineView.vue";
import TrendsView from "./views/report/TrendsView.vue";
import ProjectFormView from "./views/projects/ProjectFormView.vue";
import ProjectsView from "./views/projects/ProjectsView.vue";
import ProjectView from "./views/projects/ProjectView.vue";

/** Routes from docs/UI_SPEC.md §1. */
export const routes: RouteRecordRaw[] = [
  { path: "/", name: "projects", component: ProjectsView },
  {
    path: "/projects/new",
    name: "project-new",
    component: ProjectFormView,
    props: (route) => ({ cloneOf: typeof route.query.from === "string" ? route.query.from : undefined }),
  },
  { path: "/projects/:projectId", name: "project", component: ProjectView, props: true },
  { path: "/projects/:projectId/edit", name: "project-edit", component: ProjectFormView, props: true },
  { path: "/analyses/:id", name: "job", component: JobView, props: true },
  {
    path: "/reports/:id",
    component: ReportLayout,
    props: (route) => ({ id: String(route.params.id) }),
    children: [
      { path: "", name: "overview", component: OverviewView },
      { path: "findings", name: "findings", component: FindingsView, props: true },
      { path: "findings/:findingId", name: "evidence", component: FindingsView, props: true },
      { path: "timeline", name: "timeline", component: TimelineView },
      { path: "trends", name: "trends", component: TrendsView },
    ],
  },
  { path: "/:pathMatch(.*)*", name: "not-found", component: NotImplemented, props: { view: "Page not found", task: "—" } },
];

export function makeRouter(history: RouterHistory = createWebHistory()) {
  return createRouter({
    history,
    routes,
    scrollBehavior: (to) => (to.hash ? { el: to.hash } : undefined),
  });
}
