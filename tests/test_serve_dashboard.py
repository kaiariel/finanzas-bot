from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from finance_bot.config import Settings
from finance_bot.db import FinanceDatabase


def _load_serve_dashboard():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "serve_dashboard.py"
    spec = importlib.util.spec_from_file_location("serve_dashboard_module", module_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    return Settings(
        telegram_bot_token="test-token",
        allowed_telegram_user_ids=None,
        telegram_user_aliases={},
        data_dir=data_dir,
        sqlite_db_path=data_dir / "finances.db",
        export_csv_path=data_dir / "movimientos.csv",
        report_html_path=tmp_path / "reports" / "finanzas.html",
        receipts_sync_dir=data_dir / "receipts",
        voices_sync_dir=data_dir / "voices",
        timezone="Europe/Madrid",
        prefer_codex_media_review=True,
        voice_transcription_enabled=False,
        voice_transcription_model="base",
        voice_transcription_device="cpu",
        voice_transcription_compute_type="int8",
    )


def _template(db: FinanceDatabase) -> int:
    return db.upsert_projection_template(
        kind="expense",
        name="Alquiler",
        default_amount_cents=65500,
        category="Alquiler",
        start_month="2026-07",
    )


def test_set_projection_status_marks_completed_with_default_amount(tmp_path) -> None:
    serve_dashboard = _load_serve_dashboard()
    settings = _settings(tmp_path)
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone)
    template_id = _template(db)

    serve_dashboard.set_projection_status(settings, template_id, "2026-07", "completed")

    occurrence = db.get_projection_occurrence(template_id, "2026-07")
    assert occurrence is not None
    assert occurrence["status"] == "completed"
    assert occurrence["amount_cents"] == 65500
    assert settings.report_html_path.exists()


def test_set_projection_status_preserves_month_amount_and_note(tmp_path) -> None:
    serve_dashboard = _load_serve_dashboard()
    settings = _settings(tmp_path)
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone)
    template_id = _template(db)
    db.set_projection_occurrence(
        template_id=template_id,
        month="2026-08",
        amount_cents=70000,
        status="pending",
        note="Subida de alquiler",
    )

    serve_dashboard.set_projection_status(settings, template_id, "2026-08", "completed")

    occurrence = db.get_projection_occurrence(template_id, "2026-08")
    assert occurrence is not None
    assert occurrence["status"] == "completed"
    assert occurrence["amount_cents"] == 70000
    assert occurrence["note"] == "Subida de alquiler"

    serve_dashboard.set_projection_status(settings, template_id, "2026-08", "pending")
    occurrence = db.get_projection_occurrence(template_id, "2026-08")
    assert occurrence is not None
    assert occurrence["status"] == "pending"
    assert occurrence["amount_cents"] == 70000


def test_set_projection_status_rejects_bad_input(tmp_path) -> None:
    serve_dashboard = _load_serve_dashboard()
    settings = _settings(tmp_path)
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone)
    template_id = _template(db)

    with pytest.raises(ValueError):
        serve_dashboard.set_projection_status(settings, template_id, "2026-07", "paid")
    with pytest.raises(KeyError):
        serve_dashboard.set_projection_status(settings, 9999, "2026-07", "completed")
    assert db.get_projection_occurrence(template_id, "2026-07") is None
