import { defineConfig } from "vitest/config";

// Kept apart from vite.config.ts so tests do not load the Tauri dev-server settings.
export default defineConfig({
  test: {
    include: ["src/**/*.test.ts"],
  },
});
