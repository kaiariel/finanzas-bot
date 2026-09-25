import pytest

from finance_bot.db import FinanceDatabase


def test_projection_matching_requires_complete_meaningful_words(tmp_path):
    db = FinanceDatabase(tmp_path / "finances.db", "Europe/Madrid", create=True)
    db.upsert_projection_template(kind="expense", name="Agua", default_amount_cents=2500, category="Suministros", start_month="2026-09")
    db.upsert_projection_template(kind="expense", name="Cu", default_amount_cents=1500, category="Deudas", start_month="2026-09")
    db.upsert_projection_template(kind="expense", name="Google", default_amount_cents=1200, category="Suscripciones", start_month="2026-09")
    for note, amount, category in [("pagado factura telefonia paraguay", 5000, "Ayuda familiar"), ("seguro salud", 2300, "Salud & Cuidado"), ("ads google", 4300, "Ocio")]:
        item = db.add_manual_transaction(kind="expense", amount_cents=amount, category=category, note=note, is_fixed=True, created_at="2026-09-04T12:00:00+02:00")
        assert db.get_transaction(item)["projection_template_id"] is None


def test_pending_statuses_include_dudoso(tmp_path) -> None:
    db = FinanceDatabase(tmp_path / "finances.db", "Europe/Madrid", create=True)

    receipt_id = db.add_receipt(
        local_path="ticket.pdf",
        drive_file_id=None,
        drive_url=None,
        telegram_message_id=None,
        caption=None,
        status="dudoso",
    )

    rows = db.list_pending_files()
    assert [row["id"] for row in rows] == [receipt_id]


def test_pending_statuses_include_missing(tmp_path) -> None:
    db = FinanceDatabase(tmp_path / "finances.db", "Europe/Madrid", create=True)

    receipt_id = db.add_receipt(
        local_path="ticket.pdf",
        drive_file_id=None,
        drive_url=None,
        telegram_message_id=None,
        caption=None,
        status="missing",
    )

    rows = db.list_pending_files()
    assert [row["id"] for row in rows] == [receipt_id]


def test_find_possible_duplicate(tmp_path) -> None:
    db = FinanceDatabase(tmp_path / "finances.db", "Europe/Madrid", create=True)
    created_at = "2026-06-01T12:00:00+02:00"
    transaction_id = db.add_manual_transaction(
        kind="expense",
        amount_cents=4500,
        category="Deudas",
        note="Tarjeta Carrefour Junio",
        store="Carrefour",
        is_fixed=True,
        created_at=created_at,
    )

    duplicate = db.find_possible_duplicate(
        kind="expense",
        amount_cents=4500,
        category="Deudas",
        store="Carrefour",
        created_at=created_at,
    )

    assert duplicate is not None
    assert duplicate["id"] == transaction_id


def test_update_transaction(tmp_path) -> None:
    db = FinanceDatabase(tmp_path / "finances.db", "Europe/Madrid", create=True)
    transaction_id = db.add_manual_transaction(
        kind="expense",
        amount_cents=999,
        category="Ocio",
        note="Cafe",
        store="Bar",
        is_fixed=False,
        created_at="2026-06-01T12:00:00+02:00",
    )

    updated = db.update_transaction(
        transaction_id,
        created_at="2026-06-02T12:00:00+02:00",
        kind="income",
        amount_cents=2500,
        category="Trabajos extra",
        note="Cobro puntual",
        store="Bizum",
        is_fixed=True,
    )

    assert updated["id"] == transaction_id
    assert updated["created_at"] == "2026-06-02T12:00:00+02:00"
    assert updated["kind"] == "income"
    assert updated["amount_cents"] == 2500
    assert updated["category"] == "Trabajos extra"
    assert updated["note"] == "Cobro puntual"
    assert updated["store"] == "Bizum"
    assert updated["is_fixed"] == 1


def test_projection_plan_roundtrip(tmp_path) -> None:
    db = FinanceDatabase(tmp_path / "finances.db", "Europe/Madrid", create=True)
    template_id = db.upsert_projection_template(
        kind="expense",
        name="Alquiler",
        default_amount_cents=70000,
        category="Alquiler",
        group_name="Gastos y suscripciones",
        start_month="2026-06",
        sort_order=1,
    )

    db.set_projection_occurrence(
        template_id=template_id,
        month="2026-06",
        amount_cents=70000,
        status="completed",
        note="Pagado",
    )

    templates = db.list_projection_templates()
    occurrences = db.list_projection_occurrences("2026-06", "2026-06")

    assert [row["id"] for row in templates] == [template_id]
    assert templates[0]["start_month"] == "2026-06"
    assert occurrences[0]["template_id"] == template_id
    assert occurrences[0]["status"] == "completed"
    assert occurrences[0]["note"] == "Pagado"


def test_update_projection_template_clears_installments(tmp_path) -> None:
    db = FinanceDatabase(tmp_path / "finances.db", "Europe/Madrid", create=True)
    template_id = db.upsert_projection_template(
        kind="expense",
        name="Pago silla",
        default_amount_cents=3600,
        category="Deudas",
        start_month="2026-06",
        installment_current=3,
        installment_total=4,
    )

    updated = db.update_projection_template(
        template_id,
        default_amount_cents=4000,
        clear_installments=True,
    )

    assert updated["default_amount_cents"] == 4000
    assert updated["installment_current"] is None
    assert updated["installment_total"] is None


def test_expense_transaction_marks_matching_projection_completed(tmp_path) -> None:
    db = FinanceDatabase(tmp_path / "finances.db", "Europe/Madrid", create=True)
    template_id = db.upsert_projection_template(
        kind="expense",
        name="hosting sered",
        default_amount_cents=3700,
        category="Suscripciones",
        start_month="2026-07",
    )

    transaction_id = db.add_manual_transaction(
        kind="expense",
        amount_cents=3700,
        category="Suscripciones",
        note="hosting sered",
        store="Sered",
        is_fixed=True,
        source_text="pago por hosting sered",
        created_at="2026-07-06T12:00:00+02:00",
    )

    occurrence = db.get_projection_occurrence(template_id, "2026-07")
    transaction = db.get_transaction(transaction_id)
    assert occurrence is not None
    assert occurrence["status"] == "completed"
    assert transaction is not None
    assert transaction["projection_template_id"] == template_id


def test_income_transaction_marks_matching_projection_completed(tmp_path) -> None:
    db = FinanceDatabase(tmp_path / "finances.db", "Europe/Madrid", create=True)
    template_id = db.upsert_projection_template(
        kind="income",
        name="Sueldo cocina Ariel",
        default_amount_cents=118000,
        category="Ingresos laborales",
        start_month="2026-07",
    )

    transaction_id = db.add_manual_transaction(
        kind="income",
        amount_cents=118000,
        category="Ingresos laborales",
        note="sueldo cocina",
        source_text="cobro de sueldo cocina",
        created_at="2026-07-06T12:00:00+02:00",
    )

    occurrence = db.get_projection_occurrence(template_id, "2026-07")
    transaction = db.get_transaction(transaction_id)
    assert occurrence is not None
    assert occurrence["status"] == "completed"
    assert transaction is not None
    assert transaction["projection_template_id"] == template_id


def test_accounts_transfers_and_goals_are_separate_from_income_expense(tmp_path) -> None:
    db = FinanceDatabase(tmp_path / "finances.db", "Europe/Madrid", create=True)
    bank = db.create_account("Banco", opening_balance_cents=10000)
    cash = db.create_account("Efectivo")
    db.add_transfer(from_account_id=bank, to_account_id=cash, amount_cents=2500)
    balances = {row["name"]: row["balance_cents"] for row in db.account_balances()}
    assert balances == {"Banco": 7500, "Efectivo": 2500}
    db.upsert_budget("2026-09", "Ocio", 5000)
    goal = db.create_savings_goal("Vacaciones", 100000, "2027-06-01")
    assert db.list_budgets("2026-09")[0]["amount_cents"] == 5000
    assert db.list_savings_goals()[0]["id"] == goal


def test_receipt_registration_is_atomic_and_marks_processed(tmp_path) -> None:
    db = FinanceDatabase(tmp_path / "finances.db", "Europe/Madrid", create=True)
    receipt_id = db.add_receipt(local_path="ticket.jpg", drive_file_id=None, drive_url=None, telegram_message_id=None, caption="Ticket", status="pending")
    ids = db.register_receipt_entries(receipt_id, [{"kind": "expense", "amount_cents": 1200, "category": "Ocio", "note": "Cafe"}])
    assert len(ids) == 1
    assert db.get_receipt(receipt_id)["status"] == "processed"
    assert db.get_transaction(ids[0])["receipt_id"] == receipt_id


def test_delayed_telegram_messages_keep_their_send_date(tmp_path) -> None:
    db = FinanceDatabase(tmp_path / "finances.db", "Europe/Madrid", create=True)

    receipt_id = db.add_receipt(
        local_path="ticket.jpg",
        drive_file_id=None,
        drive_url=None,
        telegram_message_id=10,
        caption=None,
        telegram_user_id=1,
        created_at="2026-08-31T23:50:00+02:00",
    )

    assert db.get_receipt(receipt_id)["created_at"] == "2026-08-31T23:50:00+02:00"


def test_telegram_message_already_stored_detects_redelivered_messages(tmp_path) -> None:
    from finance_bot.parser import parse_transactions

    db = FinanceDatabase(tmp_path / "finances.db", "Europe/Madrid", create=True)
    db.add_receipt(
        local_path="ticket.jpg",
        drive_file_id=None,
        drive_url=None,
        telegram_message_id=10,
        caption=None,
        telegram_user_id=1,
    )
    db.add_transaction(
        parse_transactions("gasto 5 cafe")[0], telegram_message_id=11, telegram_user_id=1
    )

    assert db.telegram_message_already_stored(1, 10)
    assert db.telegram_message_already_stored(1, 11)
    assert not db.telegram_message_already_stored(1, 12)
    assert not db.telegram_message_already_stored(2, 10)
    assert not db.telegram_message_already_stored(None, 10)


def test_multiline_telegram_message_is_saved_atomically(tmp_path, monkeypatch) -> None:
    from finance_bot.parser import parse_transactions

    db = FinanceDatabase(tmp_path / "finances.db", "Europe/Madrid", create=True)
    parsed = parse_transactions("gasto 10 cafe\ngasto 20 cena")
    original = db.add_transaction
    calls = {"count": 0}

    def fail_second(item, **metadata):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("fallo simulado")
        return original(item, **metadata)

    monkeypatch.setattr(db, "add_transaction", fail_second)
    with pytest.raises(RuntimeError, match="fallo simulado"):
        db.add_telegram_transactions(parsed, telegram_user_id=1, telegram_message_id=99)

    assert db.list_transactions() == []
    assert not db.telegram_message_already_stored(1, 99)

    monkeypatch.setattr(db, "add_transaction", original)
    assert len(db.add_telegram_transactions(parsed, telegram_user_id=1, telegram_message_id=99)) == 2
    assert db.telegram_message_already_stored(1, 99)
    assert db.add_telegram_transactions(parsed, telegram_user_id=1, telegram_message_id=99) == []


def test_delete_transaction_can_be_restored_with_its_projection(tmp_path) -> None:
    db = FinanceDatabase(tmp_path / "finances.db", "Europe/Madrid", create=True)
    template = db.upsert_projection_template(
        kind="expense", name="Internet", default_amount_cents=3000,
        category="Suministros", start_month="2026-09",
    )
    transaction_id = db.add_manual_transaction(
        kind="expense", amount_cents=3000, category="Suministros", note="Internet",
        is_fixed=True, created_at="2026-09-05T12:00:00+02:00",
    )
    db.update_transaction(transaction_id, projection_template_id=template)
    db.set_projection_occurrence(
        template_id=template, month="2026-09", amount_cents=3000, status="completed", note="",
    )
    before = dict(db.get_transaction(transaction_id))

    db.delete_transaction(transaction_id)

    assert db.get_transaction(transaction_id) is None
    assert db.get_projection_occurrence(template, "2026-09")["status"] == "pending"

    restored = db.restore_deleted_transaction(transaction_id)

    assert dict(restored) == before
    assert db.get_projection_occurrence(template, "2026-09")["status"] == "completed"


def test_delete_transaction_errors_are_explicit(tmp_path) -> None:
    import pytest

    db = FinanceDatabase(tmp_path / "finances.db", "Europe/Madrid", create=True)
    transaction_id = db.add_manual_transaction(
        kind="expense", amount_cents=500, category="Ocio", note="cine",
        created_at="2026-09-05T12:00:00+02:00",
    )

    with pytest.raises(KeyError):
        db.delete_transaction(999)
    with pytest.raises(KeyError):
        db.restore_deleted_transaction(999)  # nunca se borro
    with pytest.raises(ValueError):
        db.restore_deleted_transaction(transaction_id)  # sigue existiendo

    db.delete_transaction(transaction_id)
    db.restore_deleted_transaction(transaction_id)
    with pytest.raises(ValueError):
        db.restore_deleted_transaction(transaction_id)


def test_telegram_transactions_use_categories_learned_from_corrections(tmp_path) -> None:
    from finance_bot.parser import parse_transactions

    db = FinanceDatabase(tmp_path / "finances.db", "Europe/Madrid", create=True)
    db.learn_category("Coworking", "expense", "Alquiler")

    [first] = db.add_telegram_transactions(parse_transactions("gasto 100 coworking"), telegram_user_id=1, telegram_message_id=1)
    [other] = db.add_telegram_transactions(parse_transactions("ingreso 100 coworking"), telegram_user_id=1, telegram_message_id=2)

    assert db.get_transaction(first)["category"] == "Alquiler"
    assert db.get_transaction(first)["inference_notes"] == "[]"
    # La regla es de gastos: no se aplica a un ingreso con el mismo texto.
    assert db.get_transaction(other)["category"] == "Trabajos extra"


def test_category_rules_are_seeded_from_past_panel_corrections(tmp_path) -> None:
    import json
    import sqlite3

    path = tmp_path / "finances.db"
    db = FinanceDatabase(path, "Europe/Madrid", create=True)
    db.log_audit("update", "transaction", 1, json.dumps({
        "before": {"category": "Ocio", "note": "Enviado a mamá", "kind": "expense"},
        "after": {"category": "Ayuda familiar", "note": "Enviado a mamá", "kind": "expense"},
    }))
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM category_rules")
        connection.execute("PRAGMA user_version = 1")

    reopened = FinanceDatabase(path, "Europe/Madrid")

    assert reopened.learned_category("enviado a mama", "expense") == "Ayuda familiar"
