from finance_bot.receipt_items import parse_supermarket_receipt_items


def test_cash_fresh_ticket_items_are_split() -> None:
    parsed = parse_supermarket_receipt_items(
        """
        CASH FRESH
        AGUA MINERAL                  1x 0,69       0,69 B
        LECHE ENTERA BRICK (77212)    6x 1,15       6,90 A
        TOTAL COMPRA............................... 7,59
        TARJETA TEF................................. 7,59
        """
    )

    assert parsed is not None
    assert parsed.store == "Cash Fresh"
    assert [(item.description, item.amount_cents, item.category) for item in parsed.items] == [
        ("Agua Mineral", 69, "Alimentación"),
        ("Leche Entera Brick", 690, "Alimentación"),
    ]
    assert parsed.difference_cents == 0


def test_mas_ticket_items_and_total_difference() -> None:
    parsed = parse_supermarket_receipt_items(
        """
        MAS
        BOLSA REUT-REC MAS       1x 0,15       0,15 C
        ELIGES COPOS MAIZ 50     1x 1,50       1,50 B
        POSTRE MANZ-PLATANO      1x 1,48       1,48 B
        TOTAL COMPRA........................... 3,20
        """
    )

    assert parsed is not None
    assert [item.description for item in parsed.items] == [
        "Bolsa Reut-Rec Mas",
        "Eliges Copos Maiz 50",
        "Postre Manz-Platano",
        "Diferencia / redondeo",
    ]
    assert parsed.items[0].category == "Hogar"
    assert parsed.items[-1].amount_cents == 7
    assert parsed.items[-1].category == "Hogar"


def test_non_item_bank_pdf_is_not_supermarket_ticket() -> None:
    parsed = parse_supermarket_receipt_items(
        """
        CERTIFICADO DE MOVIMIENTOS EN CUENTA
        01/06/2026 RECIBO SERVICIOS FINANCIEROS CARREFOUR EFC S.A. -45,00 EUR
        """
    )

    assert parsed is None
