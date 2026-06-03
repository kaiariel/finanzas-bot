from __future__ import annotations

from datetime import datetime

from finance_bot.parser import ParsedTransaction


MONTHS_ES = [
    "Enero",
    "Febrero",
    "Marzo",
    "Abril",
    "Mayo",
    "Junio",
    "Julio",
    "Agosto",
    "Septiembre",
    "Octubre",
    "Noviembre",
    "Diciembre",
]

TABLE_HEADER = "| Mes | Fecha | Descripción | Categoría | Cantidad | Tipo | Tienda | Es fijo |"
TABLE_SEPARATOR = "|-----|-------|-------------|-----------|----------|------|--------|---------|"


def parse_created_at(value: str) -> datetime:
    return datetime.fromisoformat(value)


def format_month(date: datetime) -> str:
    return MONTHS_ES[date.month - 1]


def format_date(date: datetime) -> str:
    return date.strftime("%d/%m/%Y")


def format_euro(cents: int) -> str:
    return f"{cents / 100:.2f}".replace(".", ",") + " €"


def tipo_label(kind: str) -> str:
    return "Ingreso" if kind == "income" else "Egreso"


def fixed_label(is_fixed: bool) -> str:
    return "Sí" if is_fixed else "No"


def clean_cell(value: object) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "/").replace("\n", " ").strip()


def table_row_from_parsed(parsed: ParsedTransaction, date: datetime) -> list[str]:
    return [
        format_month(date),
        format_date(date),
        parsed.note,
        parsed.category,
        format_euro(parsed.amount_cents),
        tipo_label(parsed.kind),
        parsed.store,
        fixed_label(parsed.is_fixed),
    ]


def transaction_table(parsed_transactions: list[ParsedTransaction], date: datetime) -> str:
    lines = [TABLE_HEADER, TABLE_SEPARATOR]
    for parsed in parsed_transactions:
        cells = [clean_cell(value) for value in table_row_from_parsed(parsed, date)]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)

