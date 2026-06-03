from datetime import datetime

from finance_bot.formatting import transaction_table
from finance_bot.parser import amount_to_cents, parse_transaction, parse_transactions


def test_amount_to_cents_spanish_decimal() -> None:
    assert amount_to_cents("12,50") == 1250


def test_amount_to_cents_thousands() -> None:
    assert amount_to_cents("1.234,56") == 123456


def test_parse_expense_text() -> None:
    parsed = parse_transaction("gasto 12,50 mercadona comida")

    assert parsed is not None
    assert parsed.kind == "expense"
    assert parsed.amount_cents == 1250
    assert parsed.currency == "EUR"
    assert parsed.category == "Alimentación"
    assert parsed.note == "mercadona comida"
    assert parsed.store == "Mercadona"
    assert parsed.is_fixed is False


def test_parse_income_text() -> None:
    parsed = parse_transaction("ingreso 1200 nomina")

    assert parsed is not None
    assert parsed.kind == "income"
    assert parsed.amount_cents == 120000
    assert parsed.category == "Ingresos laborales"


def test_google_ads_for_client_is_reimbursable_expense() -> None:
    parsed = parse_transaction("pago 50 Google Ads cliente marketing")

    assert parsed is not None
    assert parsed.kind == "expense"
    assert parsed.amount_cents == 5000
    assert parsed.category == "Ingresos clientes"
    assert parsed.store == "Google Ads"
    assert parsed.is_fixed is False


def test_client_marketing_payment_is_still_income() -> None:
    parsed = parse_transaction("cobro 500 cliente marketing")

    assert parsed is not None
    assert parsed.kind == "income"
    assert parsed.category == "Ingresos clientes"


def test_parse_signed_income() -> None:
    parsed = parse_transaction("+250 venta bici")

    assert parsed is not None
    assert parsed.kind == "income"
    assert parsed.amount_cents == 25000


def test_free_text_supermarket_expense() -> None:
    parsed = parse_transaction("hoy gasté 20 euros en el super")

    assert parsed is not None
    assert parsed.category == "Alimentación"
    assert parsed.note == "super"
    assert parsed.store == ""


def test_recibo_defaults_to_expense() -> None:
    parsed = parse_transaction("recibo de luz 47,20")

    assert parsed is not None
    assert parsed.kind == "expense"
    assert parsed.category == "Suministros"


def test_fixed_subscription() -> None:
    parsed = parse_transaction("pago 20 euros ChatGPT")

    assert parsed is not None
    assert parsed.category == "Suscripciones"
    assert parsed.store == "ChatGPT"
    assert parsed.is_fixed is True
    assert parsed.note == "ChatGPT"


def test_samsung_installment_ignores_model_and_installment_numbers() -> None:
    parsed = parse_transaction("Es cuota por mi Samsung s24 1 de 12 descontaron 59,15 ero")

    assert parsed is not None
    assert parsed.amount_cents == 5915
    assert parsed.category == "Deudas"
    assert parsed.note == "Es cuota por mi Samsung s24 1 de 12"
    assert parsed.store == "Samsung"
    assert parsed.is_fixed is True


def test_samsung_installment_without_amount_is_not_registered() -> None:
    assert parse_transaction("Es cuota por mi Samsung s24 1 de 12") is None


def test_ambiguous_transfer_needs_clarification() -> None:
    parsed = parse_transaction("transferencia 50")

    assert parsed is not None
    assert parsed.needs_clarification is True
    assert parsed.store == "Transferencia"


def test_multiline_marks_duplicate() -> None:
    parsed = parse_transactions("gasto 3,50 cafe\ngasto 3,50 cafe")

    assert len(parsed) == 2
    assert parsed[1].note.endswith("(revisar duplicado)")


def test_comma_separated_expenses_are_split_without_breaking_decimals() -> None:
    parsed = parse_transactions("pago google cloud apps 45, peluqueria 15, remesa a paraguay 120")

    assert [(item.note, item.amount_cents, item.category, item.store) for item in parsed] == [
        ("google cloud apps", 4500, "Suscripciones", "Google Cloud"),
        ("peluqueria", 1500, "Salud & Cuidado", ""),
        ("remesa a paraguay", 12000, "Ayuda familiar", ""),
    ]


def test_transaction_table_format() -> None:
    parsed = parse_transaction("gasto 12,50 mercadona comida")

    assert parsed is not None
    assert transaction_table([parsed], datetime(2026, 6, 1)) == (
        "| Mes | Fecha | Descripción | Categoría | Cantidad | Tipo | Tienda | Es fijo |\n"
        "|-----|-------|-------------|-----------|----------|------|--------|---------|\n"
        "| Junio | 01/06/2026 | mercadona comida | Alimentación | 12,50 € | Egreso | Mercadona | No |"
    )
