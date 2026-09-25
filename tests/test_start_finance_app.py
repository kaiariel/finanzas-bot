from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest


UNUSED_PID = 999_999_999


def _load_start_finance_app():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "start_finance_app.py"
    spec = importlib.util.spec_from_file_location("start_finance_app_module", module_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_single_instance_rejects_second_lock(tmp_path) -> None:
    start_app = _load_start_finance_app()
    lock_path = tmp_path / "finance_app.lock"

    with start_app.SingleInstance(lock_path):
        with pytest.raises(start_app.AlreadyRunningError):
            with start_app.SingleInstance(lock_path):
                pass


def test_runtime_status_file_contains_process_state(tmp_path) -> None:
    start_app = _load_start_finance_app()
    status_path = tmp_path / "runtime_status.json"

    start_app._write_runtime_status(
        status_path,
        started_at="2026-07-15T10:00:00+00:00",
        bot=None,
        dashboard=None,
    )

    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["bot_running"] is False
    assert payload["dashboard_running"] is False
    assert payload["supervisor_pid"] > 0


def test_detached_command_relaunches_same_script_without_flag() -> None:
    start_app = _load_start_finance_app()

    command = start_app._detached_command(
        ["--detached", "--host", "127.0.0.1", "--port", "9999", "--no-browser"]
    )

    assert command[0] == sys.executable
    assert command[1] == "-B"
    assert Path(command[2]).name == "start_finance_app.py"
    assert "--detached" not in command
    assert command[3:] == ["--host", "127.0.0.1", "--port", "9999", "--no-browser"]


def test_detached_command_leaves_the_browser_to_the_parent() -> None:
    start_app = _load_start_finance_app()

    command = start_app._detached_command(["--detached", "--dashboard-only"])

    assert command[3:] == ["--dashboard-only", "--no-browser"]


def test_write_json_file_retries_while_the_target_is_open(tmp_path, monkeypatch) -> None:
    start_app = _load_start_finance_app()
    target = tmp_path / "runtime_status.json"
    real_replace = Path.replace
    attempts = {"count": 0}

    def flaky_replace(self: Path, other):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise PermissionError(5, "Acceso denegado")
        return real_replace(self, other)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    monkeypatch.setattr(start_app.time, "sleep", lambda _seconds: None)

    start_app._write_json_file(target, {"ok": True})

    assert attempts["count"] == 3
    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}


def test_write_json_file_falls_back_to_a_direct_write(tmp_path, monkeypatch) -> None:
    start_app = _load_start_finance_app()
    target = tmp_path / "runtime_status.json"

    def always_locked(self: Path, other):
        raise PermissionError(5, "Acceso denegado")

    monkeypatch.setattr(Path, "replace", always_locked)
    monkeypatch.setattr(start_app.time, "sleep", lambda _seconds: None)

    start_app._write_json_file(target, {"ok": True})

    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}
    assert not (tmp_path / "runtime_status.json.tmp").exists()


def test_process_is_running_recognises_this_process() -> None:
    start_app = _load_start_finance_app()

    assert start_app._process_is_running(os.getpid()) is True
    assert start_app._process_is_running(UNUSED_PID) is False
    assert start_app._process_is_running(None) is False
    assert start_app._process_is_running(0) is False


def test_cleanup_orphans_leaves_a_live_session_alone(tmp_path) -> None:
    start_app = _load_start_finance_app()
    status_path = tmp_path / "runtime_status.json"
    status_path.write_text(
        json.dumps(
            {
                "supervisor_pid": os.getpid(),
                "bot_pid": UNUSED_PID,
                "dashboard_pid": UNUSED_PID,
            }
        ),
        encoding="utf-8",
    )

    assert start_app._cleanup_orphans(status_path) == []


def test_cleanup_orphans_kills_children_left_without_supervisor(tmp_path, monkeypatch) -> None:
    start_app = _load_start_finance_app()
    status_path = tmp_path / "runtime_status.json"
    status_path.write_text(
        json.dumps({"supervisor_pid": UNUSED_PID, "bot_pid": 4242, "dashboard_pid": 4343}),
        encoding="utf-8",
    )
    killed: list[int] = []

    monkeypatch.setattr(start_app, "_is_managed_pid", lambda pid: pid in {4242, 4343})
    monkeypatch.setattr(start_app, "_kill_pid", killed.append)

    cleaned = start_app._cleanup_orphans(status_path)

    assert killed == [4242, 4343]
    assert cleaned == ["bot (PID 4242)", "panel (PID 4343)"]


def test_cleanup_orphans_ignores_a_missing_status_file(tmp_path) -> None:
    start_app = _load_start_finance_app()

    assert start_app._cleanup_orphans(tmp_path / "no-existe.json") == []


def test_parser_accepts_stop_flag() -> None:
    start_app = _load_start_finance_app()

    assert start_app.build_parser().parse_args(["--stop"]).stop is True
    assert start_app.build_parser().parse_args([]).stop is False


def test_restart_policy_backs_off_and_resets_after_a_stable_run() -> None:
    start_app = _load_start_finance_app()
    policy = start_app.RestartPolicy()

    waits = [policy.schedule(now=0.0, ran_for=1.0) for _ in range(10)]

    assert waits[:3] == [5.0, 10.0, 20.0]
    assert max(waits) == start_app.RESTART_MAX_DELAY_SECONDS
    assert policy.schedule(now=100.0, ran_for=start_app.STABLE_RUN_SECONDS) == 5.0
    assert not policy.due(104.0)
    assert policy.due(105.0)


def test_rotate_log_keeps_the_previous_file(tmp_path) -> None:
    start_app = _load_start_finance_app()
    log_path = tmp_path / "bot.log"
    log_path.write_text("x" * 20, encoding="utf-8")

    start_app._rotate_log(log_path, max_bytes=100)
    assert log_path.exists()

    start_app._rotate_log(log_path, max_bytes=10)
    assert not log_path.exists()
    assert (tmp_path / "bot.log.1").read_text(encoding="utf-8") == "x" * 20


def test_autostart_script_starts_detached_without_browser(tmp_path) -> None:
    start_app = _load_start_finance_app()

    script = start_app._autostart_script(tmp_path / "pythonw.exe")

    assert f'cd /d "{start_app.ROOT}"' in script
    assert "--detached --no-browser" in script
    assert start_app.build_parser().parse_args(["--install-autostart"]).install_autostart
