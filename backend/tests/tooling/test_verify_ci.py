import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "verify_ci", Path(__file__).resolve().parents[2] / "scripts" / "verify_ci.py"
)
assert spec is not None and spec.loader is not None
ci = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ci)
checked_version, commands, new_output, run_checks = (
    ci.checked_version,
    ci.commands,
    ci.new_output,
    ci.run_checks,
)


@pytest.mark.parametrize("run_id", ["../escape", "/outside", ".", "", "a/b", "x" * 81])
def test_output_rejects_invalid_run_id(tmp_path: Path, run_id: str) -> None:
    with pytest.raises(ValueError):
        new_output(tmp_path, run_id)


def test_output_never_reuses_or_deletes_previous_run(tmp_path: Path) -> None:
    output = new_output(tmp_path, "test-01")
    marker = output / "keep.txt"
    marker.write_text("previous evidence")
    with pytest.raises(FileExistsError):
        new_output(tmp_path, "test-01")
    assert marker.read_text() == "previous evidence"


@pytest.mark.parametrize("value", ["uv 0.11.18", "uv 0.12.50", "", "other 0.12.5"])
def test_rejects_wrong_uv(value: str) -> None:
    with pytest.raises(ValueError):
        checked_version(value, "0.12.5")


@pytest.mark.parametrize("value", ["uv 0.12.5\n", "uv 0.12.5 (example 2026-09-08)\n"])
def test_accepts_pinned_uv(value: str) -> None:
    checked_version(value, "0.12.5")


def reports(output: Path, prefix: str = "") -> None:
    (output / f"{prefix}junit.xml").write_text(
        '<testsuites><testsuite tests="1" failures="0" errors="0">'
        '<testcase name="ok"/></testsuite></testsuites>'
    )
    (output / f"{prefix}coverage.xml").write_text(
        '<coverage lines-valid="1" lines-covered="1" branches-valid="0" branches-covered="0">'
        '<line number="1" hits="1"/></coverage>'
    )


def test_all_checks_and_distinct_coverage_files(tmp_path: Path) -> None:
    output = new_output(tmp_path, "complete")
    environments = []

    def runner(command, environment):
        environments.append(environment)
        reports(output)
        reports(output, "runner-")
        return 0

    assert run_checks("uv", output, {}, runner) == 0
    summary = json.loads((output / "summary.json").read_text())
    assert summary["status"] == "passed"
    assert all(step["status"] == "passed" for step in summary["steps"])
    assert environments[-1]["COVERAGE_FILE"].endswith(".coverage-runner")
    assert environments[5]["COVERAGE_FILE"].endswith(".coverage")
    assert all(env["SEOKPAN_ENVIRONMENT"] == "test" for env in environments)


@pytest.mark.parametrize("failure_at", range(10))
def test_failure_blocks_later_checks_and_keeps_evidence(tmp_path: Path, failure_at: int) -> None:
    output = new_output(tmp_path, "failure")
    calls = 0

    def runner(command, environment):
        nonlocal calls
        reports(output)
        reports(output, "runner-")
        calls += 1
        return 9 if calls - 1 == failure_at else 0

    assert run_checks("uv", output, {}, runner) == 9
    summary = json.loads((output / "summary.json").read_text())
    assert summary["status"] == "failed"
    assert summary["steps"][failure_at]["status"] == "failed"
    assert all(step["status"] == "not_run" for step in summary["steps"][failure_at + 1 :])
    assert calls == failure_at + 1


def test_command_start_failure_is_recorded(tmp_path: Path) -> None:
    output = new_output(tmp_path, "spawn-error")

    def runner(command, environment):
        raise FileNotFoundError("do not log private exception content")

    with pytest.raises(FileNotFoundError):
        run_checks("missing", output, {}, runner)
    text = (output / "summary.json").read_text()
    assert "command_could_not_complete" in text
    assert "private exception content" not in text


def test_success_exit_without_reports_is_failure(tmp_path: Path) -> None:
    output = new_output(tmp_path, "missing-report")
    with pytest.raises(ValueError, match="Missing junit"):
        run_checks("uv", output, {}, lambda command, environment: 0)
    summary = json.loads((output / "summary.json").read_text())
    assert summary["status"] == "failed"
    assert summary["steps"][5]["reason"] == "invalid_or_missing_report"


def test_commands_keep_existing_per_domain_thresholds(tmp_path: Path) -> None:
    plan = dict(commands("uv", tmp_path))
    for domain in ("room", "game", "vote"):
        assert "--fail-under=100" in plan[f"coverage-{domain}"]
        assert f"--include=*/seokpan/{domain}/domain/*" in plan[f"coverage-{domain}"]
    assert "--cov-fail-under=80" in plan["runner-tests"]
    assert "tests/application/test_turn_resolution_runner.py" in plan["runner-tests"]
    assert plan["lock"] == ["uv", "lock", "--check"]
    assert plan["sync"] == ["uv", "sync", "--locked"]


@pytest.mark.parametrize(
    "kind,content",
    [
        ("junit", "<not-junit/>"),
        ("junit", '<testsuite tests="0"/>'),
        ("junit", '<testsuite tests="1" failures="1"/>'),
        ("junit", '<testsuite tests="1" errors="1"/>'),
        ("coverage", '<coverage lines-valid="0"/>'),
        ("coverage", "<not-coverage/>"),
        ("junit", '<testsuite tests="1" failures="0" errors="0"/>'),
        (
            "junit",
            '<testsuite tests="1" failures="0" errors="0">'
            "<testcase><skipped/></testcase></testsuite>",
        ),
        ("junit", '<testsuite tests="2" failures="0" errors="0"><testcase/></testsuite>'),
        ("junit", '<!DOCTYPE x [<!ENTITY x "unsafe">]><testsuite/>'),
        (
            "coverage",
            '<coverage lines-valid="1" lines-covered="1" branches-valid="0" branches-covered="0"/>',
        ),
        (
            "coverage",
            '<coverage lines-valid="1" lines-covered="1" branches-valid="2" branches-covered="2">'
            '<line number="1" hits="1" branch="true" condition-coverage="50% (1/2)"/></coverage>',
        ),
    ],
)
def test_rejects_empty_or_failed_report(tmp_path: Path, kind: str, content: str) -> None:
    path = tmp_path / "report.xml"
    path.write_text(content)
    with pytest.raises(ValueError):
        ci.check_report(path, kind)


@pytest.mark.parametrize("kind", ["timeout", "interrupt"])
def test_interruption_keeps_later_steps_unexecuted(tmp_path: Path, kind: str) -> None:
    output = new_output(tmp_path, kind)

    def runner(command, environment):
        if kind == "timeout":
            raise subprocess.TimeoutExpired("private-command-not-logged", 1)
        raise KeyboardInterrupt

    with pytest.raises(subprocess.TimeoutExpired if kind == "timeout" else KeyboardInterrupt):
        run_checks("uv", output, {}, runner)
    summary = json.loads((output / "summary.json").read_text())
    assert summary["exit_code"] == 1
    assert summary["steps"][0]["reason"] == ("timeout" if kind == "timeout" else "interrupted")
    assert all(step["status"] == "not_run" for step in summary["steps"][1:])
    assert "private-command" not in (output / "summary.json").read_text()


def test_source_change_after_checks_prevents_success(tmp_path: Path) -> None:
    output = new_output(tmp_path, "source-change")
    state = {"source_sha256": "before"}

    def runner(command, environment):
        assert "SEOKPAN_IDENTITY_DATABASE_URL" not in environment
        reports(output)
        reports(output, "runner-")
        state["source_sha256"] = "after"
        return 0

    with pytest.raises(ValueError, match="Sources changed"):
        run_checks(
            "uv", output, {"SEOKPAN_IDENTITY_DATABASE_URL": "not-for-ci"}, runner, lambda: state
        )
    summary = json.loads((output / "summary.json").read_text())
    assert summary["status"] == "failed"
    assert summary["reason"] == "source_changed_during_checks"


def test_execute_preserves_exit_code_and_rejects_missing_command() -> None:
    assert ci.execute([sys.executable, "-c", "raise SystemExit(7)"], os.environ, timeout=5) == 7
    with pytest.raises(FileNotFoundError):
        ci.execute([str(Path(__file__).parent / "missing-executable")], os.environ, timeout=5)


def test_execute_forwards_output_without_opening_a_console() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os,sys;sys.path.insert(0,'scripts');from verify_ci import execute;"
            "raise SystemExit(execute([sys.executable,'-c',"
            "\"import sys;print('child-out');print('child-err',file=sys.stderr)\"],os.environ))",
        ],
        cwd=ci.BACKEND,
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert "child-out" in result.stdout
    assert "child-err" in result.stderr


def test_timeout_stops_only_the_owned_process_tree(tmp_path: Path) -> None:
    record = tmp_path / "children.json"
    code = (
        "import json,subprocess,sys,time;from pathlib import Path;"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
        "Path(sys.argv[1]).write_text(json.dumps([__import__('os').getpid(),child.pid]));"
        "time.sleep(60)"
    )
    unrelated = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"])
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            ci.execute([sys.executable, "-c", code, str(record)], os.environ, timeout=2)
        assert unrelated.poll() is None
        # Windows uses a process handle query rather than os.kill(pid, 0), which
        # is not a portable liveness check for Python on Windows.
        for pid in json.loads(record.read_text()):
            if os.name == "nt":
                import ctypes
                from ctypes import wintypes

                kernel = ctypes.windll.kernel32
                kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                kernel.OpenProcess.restype = wintypes.HANDLE
                kernel.GetExitCodeProcess.argtypes = [
                    wintypes.HANDLE,
                    ctypes.POINTER(wintypes.DWORD),
                ]
                kernel.CloseHandle.argtypes = [wintypes.HANDLE]
                handle = kernel.OpenProcess(0x1000, False, pid)
                if handle:
                    try:
                        status = wintypes.DWORD()
                        assert kernel.GetExitCodeProcess(handle, ctypes.byref(status))
                        assert status.value != 259
                    finally:
                        kernel.CloseHandle(handle)
            else:
                with pytest.raises(ProcessLookupError):
                    os.kill(pid, 0)
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)
