import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { runCommand } from "./ci-process.mjs";

const options = { cwd: process.cwd(), timeoutMs: 5000, stdio: "ignore" };
test("command success, nonzero exit, and missing executable stay distinct", async () => {
  assert.equal((await runCommand(process.execPath, ["-e", "process.exit(0)"], options)).code, 0);
  const failed = await runCommand(process.execPath, ["-e", "process.exit(7)"], options);
  assert.equal(failed.code, 7);
  assert.equal(failed.reason, "command_failed");
  assert.equal(
    (await runCommand(resolve("missing-ci-executable"), [], options)).reason,
    "spawn_failed",
  );
});
test("timeout cleans the owned child and grandchild, not another process", async (t) => {
  const directory = mkdtempSync(resolve(tmpdir(), "seokpan-process-"));
  const record = resolve(directory, "child.json");
  const unrelated = spawn(process.execPath, ["-e", "setInterval(()=>{},1000)"], {
    stdio: "ignore",
    windowsHide: true,
  });
  let grandchild;
  t.after(() => {
    unrelated.kill();
    if (grandchild) {
      try {
        process.kill(grandchild);
      } catch {
        /* Already stopped. */
      }
    }
    assert.equal(dirname(directory), resolve(tmpdir()));
    rmSync(directory, { recursive: true, force: true });
  });
  const code = `const {spawn}=require('node:child_process');const {writeFileSync}=require('node:fs');const c=spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'ignore',windowsHide:true});writeFileSync(process.argv[1],JSON.stringify({parent:process.pid,child:c.pid}));setInterval(()=>{},1000);`;
  const result = await runCommand(process.execPath, ["-e", code, record], {
    ...options,
    timeoutMs: 1500,
  });
  const pids = JSON.parse(readFileSync(record, "utf8"));
  grandchild = pids.child;
  assert.equal(result.reason, "timeout");
  assert.equal(result.code, 1);
  assert.throws(() => process.kill(pids.parent, 0), { code: "ESRCH" });
  assert.throws(() => process.kill(grandchild, 0), { code: "ESRCH" });
  assert.equal(process.kill(unrelated.pid, 0), true);
});
test("invalid timeout is rejected before spawning", () => {
  assert.throws(() => runCommand(process.execPath, [], { ...options, timeoutMs: 0 }));
});
