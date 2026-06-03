from __future__ import annotations

import re
from dataclasses import dataclass, replace

from finance_bot.parser import (
    GROCERY_STORES,
    amount_to_cents,
    infer_category_from_keywords,
    infer_store,
    normalize_text,
)


LINE_AMOUNT_RE = re.compile(
    r"^(?P<description>.+?)\s+(?P<amount>-?(?:\d{1,3}(?:[.,]\d{3})*|\d+)[.,]\d{2})\s*(?:EUR|€)?\s*(?:[A-Z])?$",
    re.IGNORECASE,
)

TOTAL_RE = re.compile(
    r"\b(?:total\s+compra|total\s+a\s+pagar|importe\s+total|total)\b.*?(?P<amount>\d+(?:[.,]\d{2}))",
    re.IGNORECASE,
)

SKIP_LINE_KEYWORDS = (
    "a devolver",
    "autorizacion",
    "base:",
    "cambio",
    "contactless",
    "desglose",
    "efectivo",
    "factura",
    "fecha:",
    "hora:",
    "iva",
    "mastercard",
    "operacion",
    "subtotal",
    "tarjeta",
    "total",
    "visa",
)

RECEIPT_HINTS = (
    "total compra",
    "supermercado",
    "iva incluido",
    "cash fresh",
    "mercadona",
    "lidl",
    "carrefour",
    "aldi",
    "covap",
)


@dataclass(frozen=True)
class ReceiptItem:
    description: str
    amount_cents: int
    category: str
    store: str
    source_line: str


@dataclass(frozen=True)
class ParsedReceiptItems:
    items: list[ReceiptItem]
    store: str
    total_cents: int | None
    difference_cents: int


def _is_receipt_like(text: str, store: str) -> bool:
    normalized = normalize_text(text)
    return store in GROCERY_STORES or any(hint in normalized for hint in RECEIPT_HINTS)


def _clean_description(raw: str) -> str:
    text = re.sub(r"\([^)]*\)", " ", raw)
    text = re.sub(r"\b\d+\s*x\s*-?\d+(?:[.,]\d{2})\s*$", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\b\d+[,.]\d{3}\s*(?:kg|kgr|gr|g|l)\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"^\d+\s+", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .:-").title()


def _line_should_skip(line: str) -> bool:
    normalized = normalize_text(line)
    return any(keyword in normalized for keyword in SKIP_LINE_KEYWORDS)


def _item_category(description: str, store: str) -> str:
    normalized = normalize_text(description)
    if "bolsa" in normalized:
        return "Hogar"
    if "agua mineral" in normalized:
        return "Alimentación"
    return infer_category_from_keywords(description, store) or (
        "Alimentación" if store in GROCERY_STORES else "Hogar"
    )


def _find_total(text: str) -> int | None:
    totals = []
    for line in text.splitlines():
        normalized = normalize_text(line)
        if "subtotal" in normalized or "iva" in normalized:
            continue
        match = TOTAL_RE.search(line)
        if match:
            totals.append(amount_to_cents(match.group("amount")))
    return totals[-1] if totals else None


def parse_supermarket_receipt_items(text: str) -> ParsedReceiptItems | None:
    store = infer_store(text)
    if not _is_receipt_like(text, store):
        return None

    items: list[ReceiptItem] = []
    for line in text.splitlines():
        clean_line = " ".join(line.strip().split())
        if not clean_line or _line_should_skip(clean_line):
            continue

        match = LINE_AMOUNT_RE.match(clean_line)
        if not match:
            continue

        description = _clean_description(match.group("description"))
        if len(description) < 2:
            continue

        amount_cents = amount_to_cents(match.group("amount"))
        items.append(
            ReceiptItem(
                description=description,
                amount_cents=amount_cents,
                category=_item_category(description, store),
                store=store,
                source_line=clean_line,
            )
        )

    if len(items) < 2:
        return None

    items = _mark_item_duplicates(items)
    total_cents = _find_total(text)
    difference_cents = 0
    if total_cents is not None:
        difference_cents = total_cents - sum(item.amount_cents for item in items)
        if difference_cents:
            items.append(
                ReceiptItem(
                    description="Diferencia / redondeo",
                    amount_cents=difference_cents,
                    category="Hogar",
                    store=store,
                    source_line="Diferencia / redondeo",
                )
            )

    return ParsedReceiptItems(
        items=items,
        store=store,
        total_cents=total_cents,
        difference_cents=difference_cents,
    )


def _mark_item_duplicates(items: list[ReceiptItem]) -> list[ReceiptItem]:
    seen: set[tuple[str, int]] = set()
    marked: list[ReceiptItem] = []
    for item in items:
        key = (normalize_text(item.description), item.amount_cents)
        if key in seen and "(revisar duplicado)" not in item.description:
            item = replace(item, description=f"{item.description} (revisar duplicado)")
        seen.add(key)
        marked.append(item)
    return marked
