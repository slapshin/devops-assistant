import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, RouterProvider, type RouteObject } from "react-router";
import { Layout, NotImplemented } from "./components/Layout";
import { StartPage } from "./routes/StartPage";

/** Routes frozen by docs/UI_SPEC.md §1; views other than Start arrive in T008. */
export const routes: RouteObject[] = [
  {
    element: <Layout />,
    children: [
      { path: "/", element: <StartPage /> },
      { path: "/analyses/:id", element: <NotImplemented view="Job progress" task="T008" /> },
      { path: "/reports/:id", element: <NotImplemented view="Overview" task="T008" /> },
      { path: "/reports/:id/findings", element: <NotImplemented view="Findings" task="T008" /> },
      {
        path: "/reports/:id/findings/:findingId",
        element: <NotImplemented view="Evidence" task="T008" />,
      },
      { path: "/reports/:id/trends", element: <NotImplemented view="Trends" task="T008" /> },
      { path: "*", element: <NotImplemented view="Page not found" task="—" /> },
    ],
  },
];

export function App({ queryClient = new QueryClient() }: { queryClient?: QueryClient }) {
  const router = createBrowserRouter(routes);
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}
