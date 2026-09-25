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
    assert parsed.category == "Hogar y Alimentación"
    assert parsed.note == "mercadona comida"
    assert parsed.store == "Mercadona"
    assert parsed.is_fixed is False


def test_parse_income_text() -> None:
    parsed = parse_transaction("ingreso 1200 nomina")

    assert parsed is not None
    assert parsed.kind == "income"
    assert parsed.amount_cents == 120000
    assert parsed.category == "Ingresos laborales"
    assert parsed.inference_notes == (
        "No se pudo deducir a que ingreso concreto del mes corresponde este cobro.",
    )


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
    assert parsed.category == "Hogar y Alimentación"
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
    assert parsed.inference_notes == ()


def test_hostinger_is_subscription() -> None:
    parsed = parse_transaction("gasto 26 hostinger")

    assert parsed is not None
    assert parsed.category == "Suscripciones"
    assert parsed.store == "Hostinger"
    assert parsed.is_fixed is True


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


def test_cobro_with_expense_categories_is_treated_as_charge() -> None:
    parsed = parse_transactions(
        "cobro 22 euros seguro salud, 25 euros musica de izhan, "
        "6 euros creditos antrophic, 5 euros chatgpt creditos, "
        "20 euros apuesta por paraguay campeon mundial, 13 euros capcut"
    )

    assert [(item.kind, item.amount_cents, item.category, item.store) for item in parsed] == [
        ("expense", 2200, "Salud & Cuidado", ""),
        ("expense", 2500, "Hogar y Alimentación", ""),
        ("expense", 600, "Suscripciones", "Anthropic"),
        ("expense", 500, "Suscripciones", "ChatGPT"),
        ("expense", 2000, "Ocio", ""),
        ("expense", 1300, "Suscripciones", "Capcut"),
    ]
    assert [item.is_fixed for item in parsed] == [True, False, False, False, False, True]


def test_spoken_euro_amount_is_parsed() -> None:
    parsed = parse_transaction("pegatinas para Isan por dos euros")

    assert parsed is not None
    assert parsed.amount_cents == 200
    assert parsed.category == "Hogar y Alimentación"
    assert parsed.note == "pegatinas para Isan"


def test_audio_style_sentence_with_multiple_amounts_is_split() -> None:
    parsed = parse_transactions(
        "frutas y verduras 9,54 por vaso para Isan por un euro y pegatinas para Isan por dos euros"
    )

    assert [(item.note, item.amount_cents, item.category) for item in parsed] == [
        ("frutas y verduras", 954, "Hogar y Alimentación"),
        ("vaso para Isan", 100, "Hogar y Alimentación"),
        ("pegatinas para Isan", 200, "Hogar y Alimentación"),
    ]


def test_transaction_table_format() -> None:
    parsed = parse_transaction("gasto 12,50 mercadona comida")

    assert parsed is not None
    assert transaction_table([parsed], datetime(2026, 6, 1)) == (
        "| Mes | Fecha | Descripción | Categoría | Cantidad | Tipo | Tienda | Es fijo |\n"
        "|-----|-------|-------------|-----------|----------|------|--------|---------|\n"
        "| Junio | 01/06/2026 | mercadona comida | Hogar y Alimentación | 12,50 € | Egreso | Mercadona | No |"
    )


def test_income_never_keeps_an_expense_category() -> None:
    for text in ("ingreso 100 dinero Izhan", "ingreso 30 venta ropa", "+20 mercadona"):
        parsed = parse_transaction(text)

        assert parsed is not None
        assert parsed.kind == "income"
        assert parsed.category == "Trabajos extra", text
        assert any("es de gastos" in note for note in parsed.inference_notes)


def test_income_keyword_category_is_kept() -> None:
    parsed = parse_transaction("ingreso 1200 nomina")

    assert parsed is not None
    assert not any("es de gastos" in note for note in parsed.inference_notes)


def test_unknown_expense_is_left_unclassified() -> None:
    parsed = parse_transaction("gasto 40 regalo cumpleanos")

    assert parsed is not None
    assert parsed.category == "Sin clasificar"
    assert any("Sin clasificar" in note for note in parsed.inference_notes)


def test_learned_category_replaces_the_guess_and_its_warning() -> None:
    from finance_bot.parser import note_key, with_learned_category

    parsed = parse_transaction("gasto 100 Coworking!")
    learned = with_learned_category(parsed, "Alquiler")

    assert note_key("  Cóworking! ") == "coworking"
    assert learned.category == "Alquiler"
    assert learned.is_fixed is True
    assert not learned.inference_notes
