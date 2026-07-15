from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


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
