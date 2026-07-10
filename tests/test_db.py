from finance_bot.db import FinanceDatabase


def test_pending_statuses_include_dudoso(tmp_path) -> None:
    db = FinanceDatabase(tmp_path / "finances.db", "Europe/Madrid")

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


def test_find_possible_duplicate(tmp_path) -> None:
    db = FinanceDatabase(tmp_path / "finances.db", "Europe/Madrid")
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
    db = FinanceDatabase(tmp_path / "finances.db", "Europe/Madrid")
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
    db = FinanceDatabase(tmp_path / "finances.db", "Europe/Madrid")
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
    db = FinanceDatabase(tmp_path / "finances.db", "Europe/Madrid")
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
    db = FinanceDatabase(tmp_path / "finances.db", "Europe/Madrid")
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
    db = FinanceDatabase(tmp_path / "finances.db", "Europe/Madrid")
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
