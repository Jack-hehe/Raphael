import { defineConfig } from "vitest/config";

// Dates are shown in local time ("21:42", "今天"): the tests expect Taipei whatever the machine's
// time zone is. Set before any test runs.
process.env.TZ = "Asia/Taipei";

// Kept apart from vite.config.ts so tests do not load the Tauri dev-server settings.
export default defineConfig({
  test: {
    include: ["src/**/*.test.ts", "scripts/**/*.test.mjs"],
    // Bound process startup and native locale initialization on shared CI runners.
    // Keep per-file isolation and the default test deadline intact.
    maxWorkers: process.env.CI ? 2 : undefined,
  },
});
