import { defineConfig } from "@playwright/test";

// Isolated rendering tests. API/WS are intercepted with synthetic data; no
// backend process and no reuse of the user's localhost:5173 trial session.
export default defineConfig({
  testDir: "./e2e", testMatch: "ui-feedback.spec.ts", workers: 1, retries: 0,
  forbidOnly: true, timeout: 30_000,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:5174", browserName: "chromium", headless: true,
    viewport: { width: 1280, height: 900 }, trace: "off", video: "off", screenshot: "off",
  },
  webServer: {
    command: `"${process.execPath}" node_modules/vite/bin/vite.js --port 5174`,
    url: "http://localhost:5174", reuseExistingServer: false, timeout: 30_000,
  },
});
