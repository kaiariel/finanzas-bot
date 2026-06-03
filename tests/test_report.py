from pathlib import Path

from finance_bot.config import Settings
from finance_bot.db import FinanceDatabase
from finance_bot.parser import VALID_CATEGORIES
from finance_bot.report import render_report_html


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
        voice_transcription_enabled=False,
        voice_transcription_model="base",
        voice_transcription_device="cpu",
        voice_transcription_compute_type="int8",
    )


def test_report_includes_codex_analytics_panel(tmp_path) -> None:
    settings = _settings(tmp_path)
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone)
    db.add_manual_transaction(
        kind="expense",
        amount_cents=1250,
        category=VALID_CATEGORIES[0],
        note="Mercadona comida",
        store="Mercadona",
        created_at="2026-06-02T12:00:00+02:00",
    )
    db.upsert_projection_template(
        kind="income",
        name="Nomina",
        default_amount_cents=120000,
        category="Ingresos laborales",
        start_month="2026-06",
    )

    html = render_report_html(settings)

    assert "Analisis Codex" in html
    assert 'id="analyticsPanel"' in html
    assert 'id="analyticsRecommendations"' in html
    assert "renderAnalytics" in html
