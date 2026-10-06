"""Textos y botones de Telegram: lo que el bot entendió y cómo corregirlo con un toque.

Cada movimiento registrado se confirma mostrando tipo, importe, categoría y
concepto, con botones para cambiar el tipo, elegir otra categoría o deshacer.
Así un ingreso guardado como gasto se ve y se corrige desde el móvil, sin
abrir el panel.
"""
from __future__ import annotations

import re
from datetime import datetime

from telegram import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup

from finance_bot.formatting import format_euro
from finance_bot.parser import DEFAULT_EXPENSE_CATEGORY, VALID_CATEGORIES

CALLBACK_PREFIX = "tx"
TICKET_PROMPT_RE = re.compile(r"Ticket #(\d+)")
# "#12 21,40 mercadona" o "ticket 12 21,40 mercadona": total para un ticket concreto.
TICKET_TOTAL_RE = re.compile(r"^\s*(?:ticket\s*)?#?\s*(\d{1,6})\s+(?=\S)(.+)$", re.IGNORECASE)
TICKET_PLACEHOLDER = "21,40 Mercadona"


def kind_label(kind: str) -> str:
    return "Ingreso" if kind == "income" else "Gasto"


def _date_label(created_at: str) -> str:
    try:
        return datetime.fromisoformat(created_at).strftime("%d/%m/%Y")
    except ValueError:
        return created_at[:10]


def transaction_text(row, *, hint: str | None = None, ticket_id: int | None = None) -> str:
    """Resumen de un movimiento tal como quedó guardado."""
    store = f" · {row['store']}" if row["store"] else ""
    head = f"✅ {kind_label(row['kind'])} · {format_euro(int(row['amount_cents']))} · {row['category']}{store}"
    lines = []
    if ticket_id is not None:
        lines.append(f"📎 Ticket #{ticket_id} registrado por su total")
    lines.append(head)
    lines.append(f"«{row['note']}» · {_date_label(str(row['created_at']))} · #{row['id']}")
    if hint:
        lines.append(hint)
    elif row["category"] == DEFAULT_EXPENSE_CATEGORY:
        lines.append("⚠️ Sin categoría clara: elige una abajo.")
    return "\n".join(lines)


def deleted_text(row) -> str:
    return (
        f"🗑️ Eliminado: {kind_label(row['kind'])} · {format_euro(int(row['amount_cents']))} · «{row['note']}» · #{row['id']}\n"
        "Pulsa Restaurar si fue un error."
    )


def _button(label: str, transaction_id: int, action: str, argument: str | None = None) -> InlineKeyboardButton:
    data = f"{CALLBACK_PREFIX}:{transaction_id}:{action}" + (f":{argument}" if argument is not None else "")
    return InlineKeyboardButton(label, callback_data=data)


def transaction_keyboard(row) -> InlineKeyboardMarkup:
    toggle = "🔁 Es ingreso" if row["kind"] == "expense" else "🔁 Es gasto"
    return InlineKeyboardMarkup([
        [_button(toggle, row["id"], "kind"), _button("🏷️ Categoría", row["id"], "cats")],
        [_button("🗑️ Deshacer", row["id"], "undo")],
    ])


def category_keyboard(row) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for index, category in enumerate(VALID_CATEGORIES):
        label = ("✓ " if category == row["category"] else "") + category
        button = _button(label, row["id"], "cat", str(index))
        if index % 2 == 0:
            rows.append([button])
        else:
            rows[-1].append(button)
    toggle = "🔁 Es ingreso" if row["kind"] == "expense" else "🔁 Es gasto"
    rows.append([_button(toggle, row["id"], "kind"), _button("↩️ Volver", row["id"], "back")])
    rows.append([_button("🗑️ Deshacer", row["id"], "undo")])
    return InlineKeyboardMarkup(rows)


def restore_keyboard(transaction_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[_button("↩️ Restaurar", transaction_id, "restore")]])


def parse_callback(data: str) -> tuple[int, str, str | None] | None:
    parts = (data or "").split(":")
    if len(parts) < 3 or parts[0] != CALLBACK_PREFIX or not parts[1].isdigit():
        return None
    return int(parts[1]), parts[2], parts[3] if len(parts) > 3 else None


def category_from_argument(argument: str | None) -> str | None:
    if argument is None or not argument.isdigit():
        return None
    index = int(argument)
    return VALID_CATEGORIES[index] if 0 <= index < len(VALID_CATEGORIES) else None


def ticket_prompt(receipt_id: int, label: str = "foto") -> str:
    return (
        f"📎 Ticket #{receipt_id} guardado ({label}).\n"
        f"Responde a este mensaje con el total y el comercio, por ejemplo: {TICKET_PLACEHOLDER}\n"
        "Así queda registrado hoy. El detalle por producto es opcional y se hace desde el panel."
    )


def ticket_prompt_markup() -> ForceReply:
    return ForceReply(selective=True, input_field_placeholder=TICKET_PLACEHOLDER)


def ticket_from_reply(message) -> int | None:
    """Identifica el ticket al que responde un mensaje (respuesta al aviso del bot o '#12 ...')."""
    replied = getattr(message, "reply_to_message", None)
    replied_text = getattr(replied, "text", None) if replied is not None else None
    if replied_text:
        match = TICKET_PROMPT_RE.search(replied_text)
        if match:
            return int(match.group(1))
    match = TICKET_TOTAL_RE.match(message.text or "")
    if match:
        return int(match.group(1))
    return None


def ticket_total_text(message) -> str:
    """Texto con el total, sin el prefijo '#12' si lo lleva."""
    text = message.text or ""
    match = TICKET_TOTAL_RE.match(text)
    replied = getattr(message, "reply_to_message", None)
    if match and not (replied is not None and TICKET_PROMPT_RE.search(getattr(replied, "text", "") or "")):
        return match.group(2).strip()
    return text.strip()
