from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from finance_bot.config import Settings
from finance_bot.db import FinanceDatabase


def _load_review_pending():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "review_pending.py"
    spec = importlib.util.spec_from_file_location("review_pending_module", module_path)
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


def _receipt_with_notes(db: FinanceDatabase, receipt_id: int):
    return next(row for row in db.list_receipts() if row["id"] == receipt_id)


def test_review_image_prefers_codex_manual_review(tmp_path) -> None:
    review_pending = _load_review_pending()
    settings = _settings(tmp_path)
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone, create=True)
    image_path = tmp_path / "ticket.jpg"
    image_path.write_bytes(b"fake image bytes")
    receipt_id = db.add_receipt(
        local_path=str(image_path),
        drive_file_id=None,
        drive_url=None,
        telegram_message_id=None,
        caption=None,
        status="pending",
    )

    row = db.list_pending_files()[0]
    result = review_pending._review_image(settings, db, row)
    receipt = _receipt_with_notes(db, receipt_id)

    assert "derivado a Codex" in result
    assert receipt["status"] == "dudoso"
    assert receipt["caption"] == "imagen pendiente de revision por Codex"
    assert "Codex sustituye el OCR local" in receipt["review_notes"]


def test_review_voice_prefers_codex_manual_review(tmp_path) -> None:
    review_pending = _load_review_pending()
    settings = _settings(tmp_path)
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone, create=True)
    audio_path = tmp_path / "voice.ogg"
    audio_path.write_bytes(b"fake audio bytes")
    receipt_id = db.add_receipt(
        local_path=str(audio_path),
        drive_file_id=None,
        drive_url=None,
        telegram_message_id=None,
        caption=None,
        status="voice_pending",
    )

    row = db.list_pending_files()[0]
    result = review_pending._review_voice(settings, db, row)
    receipt = _receipt_with_notes(db, receipt_id)

    assert "derivado a Codex" in result
    assert receipt["status"] == "dudoso"
    assert receipt["caption"] == "audio pendiente de revision por Codex"
    assert "Codex sustituye la transcripcion local" in receipt["review_notes"]


def test_missing_image_is_retried_when_file_becomes_available(tmp_path) -> None:
    review_pending = _load_review_pending()
    settings = _settings(tmp_path)
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone, create=True)
    image_path = tmp_path / "recovered-ticket.jpg"
    image_path.write_bytes(b"fake image bytes")
    receipt_id = db.add_receipt(
        local_path=str(image_path),
        drive_file_id=None,
        drive_url=None,
        telegram_message_id=None,
        caption=None,
        status="missing",
    )

    row = db.list_pending_files()[0]
    result = review_pending._review_row(settings, db, row)
    receipt = _receipt_with_notes(db, receipt_id)

    assert "derivado a Codex" in result
    assert receipt["status"] == "dudoso"


def test_signed_amount_cents_accepts_euro_symbol() -> None:
    review_pending = _load_review_pending()

    assert review_pending._signed_amount_cents("Total 12,34 €") == 1234
