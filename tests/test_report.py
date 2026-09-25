from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from finance_bot.config import Settings
from finance_bot.db import FinanceDatabase
from finance_bot.parser import HOUSEHOLD_FOOD_CATEGORY, VALID_CATEGORIES
from finance_bot.report import render_report_html


def test_dashboard_calculations_in_javascript():
    import shutil
    import subprocess
    import pytest
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for frontend regression checks")
    subprocess.run([node, str(Path(__file__).with_name("dashboard_helpers.cjs"))], check=True)


def test_projection_distinguishes_plan_from_linked_actual_and_manual_status(tmp_path):
    from finance_bot.report import report_data
    settings = _settings(tmp_path)
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone, create=True)
    month = datetime.now(db.timezone).strftime("%Y-%m")
    template = db.upsert_projection_template(kind="income", name="Sueldo Ariel", default_amount_cents=110000, category="Ingresos laborales", start_month=month)
    transaction = db.add_manual_transaction(kind="income", amount_cents=130000, category="Ingresos laborales", note="Sueldo Ariel", created_at=month + "-04T12:00:00+02:00")
    manual = db.upsert_projection_template(kind="income", name="Extra", default_amount_cents=20000, category="Trabajos extra", start_month=month)
    db.set_projection_occurrence(template_id=manual, month=month, amount_cents=20000, status="completed", note="")
    result = report_data(settings)
    row = next(r for r in result["projections"]["rows"] if r["month"] == month and r["templateId"] == template)
    assert row["amountCents"] == 110000
    assert row["actualLinkedCents"] == 130000
    assert row["linkedTransactionIds"] == [transaction]
    assert "El importe registrado difiere del previsto" in row["linkWarnings"]
    assert result["projections"]["months"][0]["completedIncomeCents"] == 130000
    assert result["projections"]["months"][0]["actualIncomeCents"] == 130000


def test_report_script_data_cannot_close_script_element(tmp_path):
    settings = _settings(tmp_path)
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone, create=True)
    db.add_manual_transaction(kind="expense", amount_cents=100, category="Ocio", note='</script><script>alert("test")</script>')
    html = render_report_html(settings, editable=True)
    assert '</script><script>alert("test")' not in html
    assert '\\u003c/script>' in html


def test_overspent_budget_does_not_reduce_pending_expenses(tmp_path):
    from finance_bot.report import report_data
    settings = _settings(tmp_path)
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone, create=True)
    month = datetime.now(db.timezone).strftime("%Y-%m")
    db.upsert_projection_template(kind="expense", name=HOUSEHOLD_FOOD_CATEGORY, default_amount_cents=10000, category=HOUSEHOLD_FOOD_CATEGORY, start_month=month)
    db.add_manual_transaction(kind="expense", amount_cents=15000, category=HOUSEHOLD_FOOD_CATEGORY, note="Compra", created_at=month + "-04T12:00:00+02:00")
    result = report_data(settings)
    assert result["projections"]["months"][0]["pendingExpenseCents"] == 0


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
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone, create=True)
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
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone, create=True)
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
    assert 'id="projectionBalance"' in html
    assert 'id="themeToggle"' in html
    assert "finance-theme" in html
    assert "Me falta cobrar" in html
    assert "Me falta pagar" in html
    assert "Ya cobrado" in html
    assert "Ya pagado" in html
    assert "no es el saldo del banco" in html


def test_editable_report_includes_live_runtime_status(tmp_path) -> None:
    settings = _settings(tmp_path)
    FinanceDatabase(settings.sqlite_db_path, settings.timezone, create=True)

    editable_html = render_report_html(settings, editable=True)
    static_html = render_report_html(settings, editable=False)

    assert 'id="runtimeStatus"' in editable_html
    assert 'id="runtimeRefresh"' in editable_html
    assert "refreshRuntimeStatus" in editable_html
    assert 'id="runtimeStatus"' not in static_html


def test_report_normalizes_malformed_category_labels(tmp_path) -> None:
    settings = _settings(tmp_path)
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone, create=True)
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
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone, create=True)
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
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone, create=True)
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


def test_household_envelope_does_not_flag_every_grocery_line(tmp_path) -> None:
    from finance_bot.report import report_data

    settings = _settings(tmp_path)
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone, create=True)
    month = datetime.now(ZoneInfo(settings.timezone)).strftime("%Y-%m")
    template = db.upsert_projection_template(
        kind="expense",
        name=HOUSEHOLD_FOOD_CATEGORY,
        default_amount_cents=60000,
        category=HOUSEHOLD_FOOD_CATEGORY,
        start_month=month,
    )
    grocery = db.add_manual_transaction(
        kind="expense",
        amount_cents=185,
        category=HOUSEHOLD_FOOD_CATEGORY,
        note="Fresa platano 120 g",
        created_at=month + "-08T12:00:00+02:00",
    )
    wrong = db.add_manual_transaction(
        kind="expense",
        amount_cents=4000,
        category="Ocio",
        note="cine",
        created_at=month + "-09T12:00:00+02:00",
    )
    for transaction_id in (grocery, wrong):
        db.update_transaction(transaction_id, projection_template_id=template)

    row = next(
        r
        for r in report_data(settings)["projections"]["rows"]
        if r["month"] == month and r["templateId"] == template
    )

    assert set(row["linkedTransactionIds"]) == {grocery, wrong}
    # Solo el movimiento de otra categoria es un vinculo sospechoso; el gasto
    # distinto del presupuesto es normal en un sobre.
    assert row["linkWarnings"] == [f"Revisar vínculo con movimiento #{wrong}"]


def test_weekly_envelope_does_not_carry_over_unspent_money() -> None:
    from datetime import date

    from finance_bot.report import weekly_envelope

    # Viernes 25/09/2026. Semana: lunes 21 a domingo 27.
    today = date(2026, 9, 25)
    spent = {"2026-09-15": 1000, "2026-09-21": 3000, "2026-09-24": 1500}

    result = weekly_envelope(12000, "2026-09", today, spent)

    # Lo no gastado la semana anterior no suma: esta semana quedan 120 - 45 = 75 €.
    assert result["weekSpentCents"] == 4500
    assert result["weekLeftCents"] == 7500
    # Resto del mes: 75 € de esta semana + 3/7 de 120 € (lunes 28 a miercoles 30).
    assert result["pendingCents"] == 7500 + round(12000 * 3 / 7)
    assert result["planCents"] == round(12000 * 30 / 7)


def test_weekly_envelope_overspent_week_and_other_months() -> None:
    from datetime import date

    from finance_bot.report import weekly_envelope

    today = date(2026, 9, 25)
    overspent = {"2026-09-22": 20000}

    # Semana ya superada: no resta de las siguientes.
    assert weekly_envelope(12000, "2026-09", today, overspent)["pendingCents"] == round(12000 * 3 / 7)
    assert weekly_envelope(12000, "2026-08", today, {})["pendingCents"] == 0
    assert weekly_envelope(12000, "2026-10", today, {})["pendingCents"] == round(12000 * 31 / 7)


def test_weekly_envelope_counts_week_days_from_previous_month() -> None:
    from datetime import date

    from finance_bot.report import weekly_envelope

    # Jueves 01/10/2026: la semana empezo el lunes 28/09 y ese gasto cuenta.
    result = weekly_envelope(12000, "2026-10", date(2026, 10, 1), {"2026-09-28": 10000})

    assert result["weekLeftCents"] == 2000
    # Quedan jueves a domingo (4 de 4 dias de la semana dentro del mes) + 4 semanas completas
    # (5 a 25 de octubre) + 26 a 31 (6 dias).
    assert result["pendingCents"] == round(2000 + 12000 * 3 + 12000 * 6 / 7)


def test_household_projection_uses_weekly_budget_when_set(tmp_path) -> None:
    from finance_bot.report import report_data

    settings = _settings(tmp_path)
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone, create=True)
    month = datetime.now(ZoneInfo(settings.timezone)).strftime("%Y-%m")
    template = db.upsert_projection_template(
        kind="expense", name=HOUSEHOLD_FOOD_CATEGORY, default_amount_cents=60000,
        category=HOUSEHOLD_FOOD_CATEGORY, start_month=month,
    )
    db.set_projection_weekly_budget(template, 12000)

    row = next(
        r for r in report_data(settings)["projections"]["rows"]
        if r["month"] == month and r["templateId"] == template
    )

    assert row["weeklyBudgetCents"] == 12000
    assert row["amountCents"] < 60000  # el plan sale del semanal, no del importe mensual
    assert 0 <= row["remainingBudgetCents"] <= row["amountCents"]
