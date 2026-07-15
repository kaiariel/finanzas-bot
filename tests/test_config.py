from pathlib import Path

from finance_bot.config import Settings, _parse_bool


def _settings(tmp_path: Path, receipts_sync_dir: Path, voices_sync_dir: Path) -> Settings:
    data_dir = tmp_path / "data"
    return Settings(
        telegram_bot_token="test-token",
        allowed_telegram_user_ids=None,
        telegram_user_aliases={},
        data_dir=data_dir,
        sqlite_db_path=data_dir / "finances.db",
        export_csv_path=data_dir / "movimientos.csv",
        report_html_path=tmp_path / "reports" / "finanzas.html",
        receipts_sync_dir=receipts_sync_dir,
        voices_sync_dir=voices_sync_dir,
        timezone="Europe/Madrid",
        prefer_codex_media_review=True,
        voice_transcription_enabled=False,
        voice_transcription_model="base",
        voice_transcription_device="cpu",
        voice_transcription_compute_type="int8",
    )


def test_resolved_receipts_dir_falls_back_to_local_data_dir(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path, Path(r"G:\Mi unidad\Finanzas - Tickets"), tmp_path / "voices")

    def fake_can_prepare_dir(path: Path) -> bool:
        return path != settings.receipts_sync_dir

    monkeypatch.setattr("finance_bot.config._can_prepare_dir", fake_can_prepare_dir)

    assert settings.resolved_receipts_dir() == settings.local_receipts_fallback_dir


def test_ensure_dirs_uses_fallbacks_when_sync_dirs_are_unavailable(tmp_path, monkeypatch) -> None:
    settings = _settings(
        tmp_path,
        Path(r"G:\Mi unidad\Finanzas - Tickets"),
        Path(r"G:\Mi unidad\Finanzas - Audios"),
    )

    def fake_can_prepare_dir(path: Path) -> bool:
        if path in {settings.receipts_sync_dir, settings.voices_sync_dir}:
            return False
        path.mkdir(parents=True, exist_ok=True)
        return True

    monkeypatch.setattr("finance_bot.config._can_prepare_dir", fake_can_prepare_dir)

    settings.ensure_dirs()

    assert settings.local_receipts_fallback_dir.is_dir()
    assert settings.local_voices_fallback_dir.is_dir()


def test_parse_bool_accepts_accented_si() -> None:
    assert _parse_bool("sí") is True


def test_parse_bool_accepts_uppercase_accented_si() -> None:
    assert _parse_bool("SÍ") is True
