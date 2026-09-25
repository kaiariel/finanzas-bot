from __future__ import annotations

from pathlib import Path

import pytest

from finance_bot.config import Settings
from finance_bot.db import FinanceDatabase
from finance_bot.storage import DatabaseUnavailableError, create_backup, inspect_database, read_backup_status


def _settings(tmp_path: Path) -> Settings:
    data = tmp_path / "data"
    return Settings(
        telegram_bot_token="",
        allowed_telegram_user_ids=None,
        telegram_user_aliases={},
        data_dir=data,
        sqlite_db_path=tmp_path / "local" / "finances.db",
        export_csv_path=data / "movimientos.csv",
        report_html_path=tmp_path / "report.html",
        receipts_sync_dir=data / "receipts",
        voices_sync_dir=data / "voices",
        timezone="Europe/Madrid",
        prefer_codex_media_review=True,
        voice_transcription_enabled=False,
        voice_transcription_model="base",
        voice_transcription_device="cpu",
        voice_transcription_compute_type="int8",
    )


def test_missing_database_is_not_created_implicitly(tmp_path) -> None:
    path = tmp_path / "missing" / "finances.db"

    with pytest.raises(DatabaseUnavailableError, match="no se ha creado una base vacía"):
        FinanceDatabase(path, "Europe/Madrid")

    assert not path.exists()


def test_explicit_new_database_and_verified_backup(tmp_path) -> None:
    settings = _settings(tmp_path)
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone, create=True)
    db.add_manual_transaction(kind="expense", amount_cents=1250, category="Ocio", note="Café")

    backup = create_backup(settings, include_media=False)

    assert backup["counts"]["transactions"] == 1
    assert inspect_database(Path(backup["database"]), check_integrity=True)["counts"]["transactions"] == 1
    assert read_backup_status(settings.data_dir)["sha256"] == backup["sha256"]
