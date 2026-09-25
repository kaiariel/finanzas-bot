from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finance_bot.config import Settings
from finance_bot.db import FinanceDatabase
from finance_bot.parser import DEFAULT_EXPENSE_CATEGORY, VALID_CATEGORIES, amount_to_cents
from finance_bot.report import generate_report


def _value(row: dict[str, str], *names: str) -> str:
    for name in names:
        if row.get(name):
            return row[name].strip()
    return ""


def read_rows(path: Path) -> list[dict[str, object]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        rows = []
        for number, raw in enumerate(csv.DictReader(handle, dialect=dialect), 2):
            kind = _value(raw, "Tipo", "type", "kind").lower()
            kind = "income" if kind in {"income", "ingreso", "entrada"} else "expense"
            amount = amount_to_cents(_value(raw, "Cantidad", "Importe", "amount"))
            category = _value(raw, "Categoría", "Categoria", "category") or DEFAULT_EXPENSE_CATEGORY
            if category not in VALID_CATEGORIES:
                raise ValueError(f"Línea {number}: categoría no válida: {category}")
            date = _value(raw, "Fecha", "date") or datetime.now().strftime("%Y-%m-%d")
            note = _value(raw, "Descripción", "Descripcion", "description", "Concepto") or "Importado desde CSV"
            rows.append({"kind": kind, "amount_cents": amount, "category": category, "note": note, "store": _value(raw, "Tienda", "Comercio", "store"), "date": date})
        return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Previsualiza y confirma una importación CSV")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--confirm", action="store_true", help="Registra las líneas después de previsualizarlas")
    args = parser.parse_args()
    settings = Settings.from_env()
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone)
    rows = read_rows(args.csv_path)
    duplicates = []
    for index, row in enumerate(rows):
        duplicate = db.find_possible_duplicate(kind=str(row["kind"]), amount_cents=int(row["amount_cents"]), category=str(row["category"]), store=str(row["store"]), created_at=str(row["date"]))
        if duplicate:
            duplicates.append({"row": index + 2, "existingId": duplicate["id"], "description": duplicate["note"]})
    preview = {"file": str(args.csv_path), "rows": rows, "duplicates": duplicates}
    if not args.confirm:
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        print("\nVista previa solamente. Repite con --confirm para registrar las líneas.", file=sys.stderr)
        return
    if duplicates:
        raise SystemExit("Hay posibles duplicados; revisa la vista previa antes de confirmar.")
    created = []
    for row in rows:
        created_at = datetime.fromisoformat(str(row["date"])).replace(hour=12, tzinfo=db.timezone).isoformat(timespec="seconds")
        created.append(db.add_manual_transaction(kind=str(row["kind"]), amount_cents=int(row["amount_cents"]), category=str(row["category"]), note=str(row["note"]), store=str(row["store"]), source_text=f"Importación CSV: {args.csv_path.name}", created_at=created_at))
    generate_report(settings)
    print(json.dumps({"created_transaction_ids": created, "count": len(created)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
