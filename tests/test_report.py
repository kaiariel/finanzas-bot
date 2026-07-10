from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from finance_bot.config import Settings
from finance_bot.db import FinanceDatabase
from finance_bot.parser import HOUSEHOLD_FOOD_CATEGORY, VALID_CATEGORIES
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
        prefer_codex_media_review=True,
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

    assert "Diagnóstico" in html
    assert 'id="analyticsPanel"' in html
    assert 'id="analyticsRecommendations"' in html
    assert "renderAnalytics" in html
    assert 'id="tableCategoryFilter"' in html
    assert 'id="amountSort"' in html
    assert 'id="incomeList"' in html
    assert 'id="dailyExpenseMeta"' in html
    assert 'id="analyticsAiAlerts"' in html


def test_report_includes_quick_status_and_trend_chart(tmp_path) -> None:
    settings = _settings(tmp_path)
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone)
    db.upsert_projection_template(
        kind="expense",
        name="Alquiler",
        default_amount_cents=65500,
        category="Alquiler",
        start_month="2026-06",
    )

    html = render_report_html(settings)

    assert 'id="trendChart"' in html
    assert "drawTrendChart" in html
    assert "quickStatusButton" in html
    assert "setProjectionStatus" in html
    assert "status-chip" in html
    assert 'id="cashflowGrid"' in html


def test_report_normalizes_malformed_category_labels(tmp_path) -> None:
    settings = _settings(tmp_path)
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone)
    transaction_id = db.add_manual_transaction(
        kind="expense",
        amount_cents=954,
        category="Alimentación",
        note="frutas y verduras",
        created_at="2026-06-05T10:43:33+02:00",
    )
    db.update_transaction(transaction_id, category="Alimentaci?n")

    html = render_report_html(settings)

    assert "Alimentaci?n" not in html
    assert "Hogar y Alimentación" in html


def test_report_includes_ai_alert_logic_for_unmatched_or_uncertain_rows(tmp_path) -> None:
    settings = _settings(tmp_path)
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone)
    db.add_manual_transaction(
        kind="income",
        amount_cents=120000,
        category="Ingresos laborales",
        note="nomina",
        source_text="ingreso 1200 nomina",
        created_at="2026-07-05T10:00:00+02:00",
    )

    html = render_report_html(settings)

    assert "Avisos de IA" in html
    assert "aiAlertsForMonth" in html
    assert "inferenceNotes" in html


def test_household_food_projection_tracks_actual_spend(tmp_path) -> None:
    settings = _settings(tmp_path)
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone)
    month = datetime.now(ZoneInfo(settings.timezone)).strftime("%Y-%m")
    db.upsert_projection_template(
        kind="expense",
        name=HOUSEHOLD_FOOD_CATEGORY,
        default_amount_cents=60000,
        category=HOUSEHOLD_FOOD_CATEGORY,
        start_month=month,
    )
    db.add_manual_transaction(
        kind="expense",
        amount_cents=17500,
        category=HOUSEHOLD_FOOD_CATEGORY,
        note="compra supermercado",
        created_at=month + "-08T12:00:00+02:00",
    )

    html = render_report_html(settings)

    assert '"tracksActualCategory": true' in html
    assert '"actualSpentCents": 17500' in html
    assert '"remainingBudgetCents": 42500' in html
