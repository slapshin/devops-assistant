import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vitest/config";

const backend = process.env.VITE_BACKEND_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: { "/api": backend },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["src/test-setup.ts"],
  },
});
