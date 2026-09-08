import { spawnSync } from "node:child_process";

// Reuse the npm instance that invoked this script, including portable Windows
// installations. Do not accidentally select a different global npm from PATH.
const npm = process.env.npm_execpath;
if (!npm) throw new Error("Run this command with npm run verify.");
for (const task of ["api:check", "typecheck", "test", "build"]) {
  const result = spawnSync(process.execPath, [npm, "run", task], { stdio: "inherit" });
  if (result.error || result.status !== 0) process.exit(result.status ?? 1);
}
