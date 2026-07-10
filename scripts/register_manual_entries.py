from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finance_bot.config import Settings
from finance_bot.db import FinanceDatabase
from finance_bot.parser import FIXED_CATEGORIES, VALID_CATEGORIES, amount_to_cents
from finance_bot.report import generate_report


TYPE_TO_KIND = {
    "Ingreso": "income",
    "Egreso": "expense",
    "income": "income",
    "expense": "expense",
}


def _receipt_transaction_count(db: FinanceDatabase, receipt_id: int) -> int:
    with db._connect() as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM transactions WHERE receipt_id = ?",
            (receipt_id,),
        ).fetchone()
    return int(row[0]) if row is not None else 0


def _load_payload(path_arg: str) -> dict:
    if path_arg == "-":
        return json.loads(sys.stdin.read())

    return json.loads(Path(path_arg).read_text(encoding="utf-8-sig"))


def _get(entry: dict, *names: str, default=None):
    for name in names:
        if name in entry:
            return entry[name]
    return default


def _amount_cents(value) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value * 100))

    text = str(value).strip().replace("EUR", "").replace("€", "").strip()
    return amount_to_cents(text)


def _fixed_value(value, category: str) -> bool:
    if value is None or value == "":
        return category in FIXED_CATEGORIES
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"si", "sí", "yes", "true", "1"}


def _created_at(value, timezone: str) -> str | None:
    if not value:
        return None

    zone = ZoneInfo(timezone)
    text = str(value).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed.replace(hour=12, tzinfo=zone).isoformat(timespec="seconds")

    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=zone)
    return parsed.isoformat(timespec="seconds")


def _normalized_entry(
    entry: dict,
    *,
    settings: Settings,
    default_source_text: str,
    default_receipt_path: str | None,
    receipt_id: int | None,
) -> dict[str, object]:
    category = _get(entry, "category", "Categoría")
    if category not in VALID_CATEGORIES:
        raise SystemExit(f"Categoría no válida: {category}")

    type_value = _get(entry, "type", "Tipo", default="Egreso")
    kind = TYPE_TO_KIND.get(type_value)
    if kind is None:
        raise SystemExit(f"Tipo no válido: {type_value}")

    note = _get(entry, "description", "Descripción", "note", default="")
    if not str(note).strip():
        raise SystemExit("Cada entrada necesita description/Descripción")

    return {
        "kind": kind,
        "amount_cents": _amount_cents(_get(entry, "amount", "Cantidad")),
        "currency": _get(entry, "currency", "Moneda", default="EUR"),
        "category": category,
        "note": str(note).strip(),
        "store": str(_get(entry, "store", "Tienda", default="") or "").strip(),
        "is_fixed": _fixed_value(_get(entry, "is_fixed", "Es fijo"), category),
        "source_text": str(_get(entry, "source_text", default=default_source_text) or ""),
        "created_at": _created_at(_get(entry, "date", "Fecha"), settings.timezone),
        "receipt_local_path": str(
            _get(entry, "receipt_local_path", default=default_receipt_path) or ""
        ),
        "receipt_id": receipt_id,
    }


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Uso: python scripts/register_manual_entries.py entradas.json")

    settings = Settings.from_env()
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone)
    payload = _load_payload(sys.argv[1])

    receipt = None
    receipt_id = payload.get("receipt_id")
    if receipt_id is not None:
        receipt_id = int(receipt_id)
        receipt = db.get_receipt(receipt_id)
        if receipt is None:
            raise SystemExit(f"No existe el pendiente #{receipt_id}")
        if _receipt_transaction_count(db, receipt_id) and not payload.get(
            "allow_existing_receipt_entries", False
        ):
            raise SystemExit(
                f"El pendiente #{receipt_id} ya tiene movimientos enlazados"
            )

    default_source_text = payload.get("source_text") or (receipt["caption"] if receipt else "")
    default_receipt_path = payload.get("receipt_local_path") or (
        receipt["local_path"] if receipt else None
    )
    default_user = {
        "telegram_message_id": receipt["telegram_message_id"] if receipt else None,
        "telegram_user_id": receipt["telegram_user_id"] if receipt else None,
        "telegram_username": receipt["telegram_username"] if receipt else None,
        "telegram_full_name": receipt["telegram_full_name"] if receipt else None,
    }

    prepared_entries = [
        _normalized_entry(
            entry,
            settings=settings,
            default_source_text=default_source_text,
            default_receipt_path=default_receipt_path,
            receipt_id=receipt_id,
        )
        for entry in payload.get("entries", [])
    ]
    if not prepared_entries:
        raise SystemExit("No hay entries para registrar")

    created_ids: list[int] = []
    for prepared in prepared_entries:
        created_id = db.add_manual_transaction(
            kind=str(prepared["kind"]),
            amount_cents=int(prepared["amount_cents"]),
            currency=str(prepared["currency"]),
            category=str(prepared["category"]),
            note=str(prepared["note"]),
            store=str(prepared["store"]),
            is_fixed=bool(prepared["is_fixed"]),
            source_text=str(prepared["source_text"]),
            created_at=prepared["created_at"],
            receipt_local_path=str(prepared["receipt_local_path"]),
            receipt_id=prepared["receipt_id"],
            **default_user,
        )
        created_ids.append(created_id)

    if receipt_id is not None and payload.get("mark_processed", True):
        db.update_receipt_status(receipt_id, "processed")

    output = generate_report(settings)
    print(
        json.dumps(
            {
                "created_transaction_ids": created_ids,
                "receipt_id": receipt_id,
                "report": str(output.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
