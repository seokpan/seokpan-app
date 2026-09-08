import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

// Keep downloads inside this checkout; never use personal Chrome/Edge profiles.
const root = fileURLToPath(new URL("../", import.meta.url));
if (process.versions.node !== "24.19.0") throw new Error("E2E requires Node 24.19.0.");
const [command, ...args] = process.argv.slice(2);
if (!["install", "test"].includes(command)) throw new Error("Use install or test.");
if (command === "test") {
  const python = fileURLToPath(new URL(process.platform === "win32"
    ? "../../backend/.venv/Scripts/python.exe" : "../../backend/.venv/bin/python", import.meta.url));
  const version = spawnSync(python, ["--version"], { encoding: "utf8" });
  if (version.status !== 0 || version.stdout.trim() !== "Python 3.13.15") {
    throw new Error("E2E requires backend/.venv with Python 3.13.15 and uv sync --locked.");
  }
}
const result = spawnSync(process.execPath, [
  "node_modules/@playwright/test/cli.js", command,
  ...(command === "install" ? ["chromium", "--only-shell"] : args),
], {
  cwd: root, stdio: "inherit",
  env: { ...process.env, PLAYWRIGHT_BROWSERS_PATH: fileURLToPath(new URL("../.browser-cache", import.meta.url)) },
});
if (result.error) throw result.error;
process.exit(result.status ?? 1);
