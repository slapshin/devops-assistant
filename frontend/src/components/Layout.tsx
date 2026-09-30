import type { ReactNode } from "react";
import { Link, Outlet } from "react-router";
import styles from "./Layout.module.css";

export function Layout() {
  return (
    <>
      <header className={styles.header}>
        <Link to="/" className={styles.brand}>
          DevOps AI Assistant
        </Link>
      </header>
      <main className={styles.main}>
        <Outlet />
      </main>
    </>
  );
}

/** Marks a route that exists in the UI spec but is implemented by a later task. */
export function NotImplemented({ view, task, children }: { view: string; task: string; children?: ReactNode }) {
  return (
    <section aria-labelledby="ni-title" className={styles.notice}>
      <h1 id="ni-title">{view}</h1>
      <p>
        Not implemented yet — delivered by {task}. See docs/UI_SPEC.md.
      </p>
      {children}
    </section>
  );
}
