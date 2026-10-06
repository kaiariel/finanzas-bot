"""Bandeja única del panel y sus acciones: total de ticket, categoría rápida, registrar o vincular conceptos."""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from finance_bot.db import FinanceDatabase, SUMMARY_REVIEW_STATUS
from finance_bot.report import report_data
from test_serve_dashboard import _load_serve_dashboard, _settings


@pytest.fixture
def env(tmp_path):
    settings = _settings(tmp_path)
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone, create=True)
    module = _load_serve_dashboard()
    handler = object.__new__(module.DashboardHandler)
    handler.settings = settings
    return settings, db, module, handler


def _month(db) -> str:
    return datetime.now(db.timezone).strftime("%Y-%m")


def test_inbox_lists_each_pending_decision_once(env):
    settings, db, module, handler = env
    month = _month(db)
    receipt = db.add_receipt(local_path="t.jpg", drive_file_id=None, drive_url=None, telegram_message_id=None, caption=None, status="dudoso")
    unclear = db.add_manual_transaction(kind="expense", amount_cents=1100, category="Sin clasificar", note="Desayuno fuera", created_at=month + "-04T09:00:00+02:00")
    reviewed = db.add_manual_transaction(kind="expense", amount_cents=500, category="Sin clasificar", note="Varios", review_status="reviewed", created_at=month + "-04T09:00:00+02:00")
    # Marcado como cobrado sin movimiento: antes no contaba en ningun sitio.
    salary = db.upsert_projection_template(kind="income", name="Sueldo Dahiana", default_amount_cents=45500, category="Ingresos laborales", start_month="2026-06")
    db.set_projection_occurrence(template_id=salary, month=month, amount_cents=45500, status="completed", note="")
    envelope = db.upsert_projection_template(kind="expense", name="Hogar y Alimentación", default_amount_cents=60000, category="Hogar y Alimentación", start_month="2026-06")
    db.set_projection_occurrence(template_id=envelope, month=month, amount_cents=60000, status="completed", note="")
    # Mismo importe, dia y concepto: uno por texto y otro por ticket es un posible duplicado.
    first = db.add_manual_transaction(kind="expense", amount_cents=872, category="Hogar y Alimentación", note="Frutas", created_at=month + "-03T10:00:00+02:00")
    second = db.add_manual_transaction(kind="expense", amount_cents=872, category="Hogar y Alimentación", note="frutas", receipt_id=receipt, created_at=month + "-03T18:00:00+02:00")
    # Dos lineas iguales del mismo ticket no lo son.
    db.add_manual_transaction(kind="expense", amount_cents=15, category="Hogar y Alimentación", note="Bolsa", receipt_id=receipt, created_at=month + "-03T18:00:00+02:00")
    db.add_manual_transaction(kind="expense", amount_cents=15, category="Hogar y Alimentación", note="Bolsa", receipt_id=receipt, created_at=month + "-03T18:00:00+02:00")

    inbox = report_data(settings)["inbox"]
    assert inbox["receipts"] == [receipt]
    assert inbox["unclassified"] == [unclear]
    assert reviewed not in inbox["unclassified"]
    assert [(item["name"], item["month"], item["amountCents"]) for item in inbox["manualCompleted"]] == [("Sueldo Dahiana", month, 45500)]
    assert inbox["duplicates"] == [[first, second]]
    assert inbox["count"] == 4


def test_register_receipt_total_from_panel_counts_on_ticket_date_and_can_be_detailed_later(env):
    settings, db, module, handler = env
    receipt = db.add_receipt(local_path="t.jpg", drive_file_id=None, drive_url=None, telegram_message_id=None, caption=None, created_at="2026-10-03T19:30:00+02:00")
    handler._register_receipt_total(receipt, {"amount": "21,40", "store": "mercadona"})
    [row] = db.list_transactions()
    assert row["amount_cents"] == 2140 and row["store"] == "Mercadona" and row["category"] == "Hogar y Alimentación"
    assert row["review_status"] == SUMMARY_REVIEW_STATUS and row["created_at"].startswith("2026-10-03")
    assert db.get_receipt(receipt)["status"] == "processed"
    with pytest.raises(ValueError):
        handler._register_receipt_total(receipt, {"amount": "21,40", "store": "mercadona"})

    handler._review_receipt(receipt, {"entries": [{"description": "Leche", "amount": "2,10", "category": "Hogar y Alimentación", "kind": "expense"}, {"description": "Pilas", "amount": "19,30", "category": "Suministros", "kind": "expense"}], "expectedTotal": "21,40"})
    rows = db.list_transactions()
    assert [r["note"] for r in rows] == ["Leche", "Pilas"]
    assert db.get_transaction(row["id"]) is None
    assert settings.report_html_path.exists()


def test_quick_update_learns_category_marks_reviewed_and_links(env):
    settings, db, module, handler = env
    month = _month(db)
    transaction = db.add_manual_transaction(kind="expense", amount_cents=10000, category="Sin clasificar", note="coworking", created_at=month + "-05T09:00:00+02:00")
    handler._quick_update(transaction, {"category": "Alquiler"})
    row = db.get_transaction(transaction)
    assert row["category"] == "Alquiler" and row["review_status"] == "reviewed" and row["is_fixed"] == 1
    assert db.learned_category("coworking", "expense") == "Alquiler"

    handler._quick_update(transaction, {"kind": "income"})
    row = db.get_transaction(transaction)
    assert row["kind"] == "income" and row["category"] == "Trabajos extra"
    handler._quick_update(transaction, {"kind": "expense"})
    assert db.get_transaction(transaction)["category"] == "Sin clasificar"

    template = db.upsert_projection_template(kind="expense", name="Coworking", default_amount_cents=10000, category="Alquiler", start_month="2026-06")
    db.set_projection_occurrence(template_id=template, month=month, amount_cents=10000, status="completed", note="")
    assert report_data(settings)["inbox"]["manualCompleted"][0]["templateId"] == template
    handler._quick_update(transaction, {"projectionTemplateId": template})
    assert db.get_transaction(transaction)["projection_template_id"] == template
    assert report_data(settings)["inbox"]["manualCompleted"] == []
    with pytest.raises(ValueError):
        handler._quick_update(transaction, {"category": "No existe"})


def test_register_endpoint_creates_the_movement_once_and_projection_form_follows_status(env):
    settings, db, module, handler = env
    template = db.upsert_projection_template(kind="income", name="Paro", default_amount_cents=55500, category="Ingresos laborales", start_month="2026-06")
    db.set_projection_occurrence(template_id=template, month="2026-08", amount_cents=55500, status="completed", note="")
    assert db.register_projection_payment(template, "2026-08") is not None
    assert db.register_projection_payment(template, "2026-08") is None
    [row] = db.list_transactions()
    assert row["kind"] == "income" and row["amount_cents"] == 55500 and row["created_at"].startswith("2026-08-31")

    other = db.upsert_projection_template(kind="expense", name="Gimnasio", default_amount_cents=5000, category="Salud & Cuidado", start_month="2026-06")
    payload = {"name": "Gimnasio", "category": "Salud & Cuidado", "amount": "50", "status": "completed", "kind": "expense", "startMonth": "2026-06"}
    handler._update_projection(other, "2026-09", payload)
    linked = [r for r in db.list_transactions() if r["projection_template_id"] == other]
    assert len(linked) == 1
    handler._update_projection(other, "2026-09", {**payload, "status": "pending"})
    assert [r for r in db.list_transactions() if r["projection_template_id"] == other] == []


def test_deleting_the_only_movement_of_a_ticket_returns_it_to_the_queue(env):
    settings, db, module, handler = env
    receipt = db.add_receipt(local_path="t.jpg", drive_file_id=None, drive_url=None, telegram_message_id=None, caption=None)
    [line] = db.register_receipt_entries(receipt, [{"kind": "expense", "amount_cents": 500, "category": "Ocio", "note": "Café"}])
    assert db.get_receipt(receipt)["status"] == "processed"
    db.delete_transaction(line)
    assert db.get_receipt(receipt)["status"] == "pending"
    assert [row["id"] for row in db.list_pending_files()] == [receipt]
    db.restore_deleted_transaction(line)
    assert db.get_receipt(receipt)["status"] == "processed"
    assert json.loads([row for row in db.list_transactions()][0]["inference_notes"]) == []
