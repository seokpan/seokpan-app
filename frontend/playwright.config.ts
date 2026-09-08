import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";

const backend = fileURLToPath(new URL("../backend/", import.meta.url));
const python = fileURLToPath(new URL(process.platform === "win32"
  ? "../backend/.venv/Scripts/python.exe" : "../backend/.venv/bin/python", import.meta.url));

export default defineConfig({
  testDir: "./e2e",
  testMatch: "first-success.spec.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: true,
  timeout: 150_000,
  expect: { timeout: 12_000 },
  reporter: [["list"], ["json", { outputFile: "test-results/browser-e2e.json" }]],
  use: {
    baseURL: "http://localhost:5175",
    browserName: "chromium",
    headless: true,
    viewport: { width: 1280, height: 900 },
    actionTimeout: 10_000,
    navigationTimeout: 15_000,
    acceptDownloads: false,
    // Traces/HAR can contain cookies, passwords and CSRF response bodies.
    trace: "off", video: "off", screenshot: "off",
  },
  webServer: [
    {
      command: `"${python}" -m uvicorn seokpan.development:create_browser_e2e_app --factory --host 127.0.0.1 --port 8001 --workers 1 --no-access-log`,
      cwd: backend,
      url: "http://127.0.0.1:8001/health/live",
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: `"${process.execPath}" node_modules/vite/bin/vite.js --config vite.e2e.config.ts`,
      url: "http://localhost:5175",
      reuseExistingServer: false,
      timeout: 30_000,
    },
  ],
});
