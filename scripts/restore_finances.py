from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finance_bot.config import Settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Restaura un backup completo de Finanzas")
    parser.add_argument("backup_db", type=Path)
    parser.add_argument("--confirm", action="store_true", help="Sobrescribe la base local y restaura adjuntos")
    args = parser.parse_args()
    settings = Settings.from_env()
    source = args.backup_db.resolve()
    if not source.exists():
        raise SystemExit(f"No existe el backup: {source}")
    with sqlite3.connect(source) as connection:
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise SystemExit("El backup no supera PRAGMA quick_check")
    if not args.confirm:
        print(f"Backup válido: {source}. Repite con --confirm para restaurarlo.")
        return 0
    settings.sqlite_db_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, settings.sqlite_db_path)
    stamp = source.stem.removeprefix("finances-")
    for label, destination in (("receipts", settings.resolved_receipts_dir()), ("voices", settings.resolved_voices_dir())):
        source_dir = source.parent / f"{label}-{stamp}"
        if source_dir.exists():
            shutil.copytree(source_dir, destination, dirs_exist_ok=True)
    print(f"Restaurado: {settings.sqlite_db_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
