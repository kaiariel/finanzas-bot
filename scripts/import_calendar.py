"""Importa eventos revisados desde JSON; no duplica fuentes ni modifica finanzas."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from finance_bot.calendar import CalendarStore, validate_event
from finance_bot.config import Settings
from finance_bot.db import FinanceDatabase
from finance_bot.storage import inspect_database


def import_events(db: FinanceDatabase, entries: list[dict]) -> list[int]:
    if not isinstance(entries, list) or not entries:
        raise ValueError("La importación debe contener una lista de eventos.")
    for entry in entries:
        validate_event(entry)
        if not isinstance(entry.get("sourceKey"), str) or not entry["sourceKey"]:
            raise ValueError("Cada entrada debe tener una clave de fuente para evitar duplicados.")
        source = entry.get("sourcePath")
        if source and (Path(source).suffix.lower() not in {".pdf", ".png", ".jpg", ".jpeg"} or not Path(source).is_file()):
            raise ValueError("No se encuentra un documento de origen compatible.")
    if len({entry["sourceKey"] for entry in entries}) != len(entries):
        raise ValueError("Hay claves de fuente duplicadas dentro de la importación.")
    store = CalendarStore(db)
    with db.atomic():
        return [store.save_event(entry, source_key=entry["sourceKey"], source_label=entry.get("sourceLabel", ""), source_path=entry.get("sourcePath", "")) for entry in entries]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--database", type=Path, help="Base alternativa para pruebas")
    args = parser.parse_args()
    entries = json.loads(args.input.read_text(encoding="utf-8"))
    # Validar antes de abrir la base (su apertura puede actualizar el esquema).
    for entry in entries:
        validate_event(entry)
    settings = Settings.from_env()
    database = args.database or settings.sqlite_db_path
    inspect_database(database, check_integrity=True)
    backup_dir = settings.data_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / ("pre-calendar-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".db")
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as original:
        with sqlite3.connect(backup) as target:
            original.backup(target)
            if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("No se pudo verificar la copia previa.")
    db = FinanceDatabase(database, settings.timezone)
    before = len(CalendarStore(db).list_events())
    ids = import_events(db, entries)
    print(json.dumps({"ok": True, "added": len(CalendarStore(db).list_events()) - before, "entries": len(ids), "backup": str(backup)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
