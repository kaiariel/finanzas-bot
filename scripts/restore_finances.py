from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
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
    backup_dir = settings.data_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    # Antes de sobrescribir nada, guarda lo que habia ahora mismo. Si el backup
    # elegido resulta ser el equivocado, "restore_finances.py" apuntando a este
    # snapshot deshace la restauracion.
    if settings.sqlite_db_path.exists():
        pre_restore_db = backup_dir / f"pre-restore-{stamp}.db"
        with sqlite3.connect(settings.sqlite_db_path) as current, sqlite3.connect(pre_restore_db) as snapshot:
            current.backup(snapshot)
        print(f"Copia de seguridad de la base actual antes de restaurar: {pre_restore_db.resolve()}")

    shutil.copy2(source, settings.sqlite_db_path)
    # SQLite puede dejar un -wal/-shm junto a la base restaurada si el backup se
    # tomo en modo WAL; al borrar la base vieja evitamos mezclar escrituras
    # pendientes de la sesion anterior con el contenido recien copiado.
    for suffix in ("-wal", "-shm"):
        stale = settings.sqlite_db_path.with_name(settings.sqlite_db_path.name + suffix)
        stale.unlink(missing_ok=True)

    restore_stamp = source.stem.removeprefix("finances-")
    for label, destination in (("receipts", settings.resolved_receipts_dir()), ("voices", settings.resolved_voices_dir())):
        source_dir = source.parent / f"{label}-{restore_stamp}"
        if not source_dir.exists():
            continue
        if destination.exists():
            # Se renombra en vez de fusionar (dirs_exist_ok mezclaria archivos de
            # la sesion actual con los del backup), y queda guardada junto al
            # resto de copias de seguridad por si hay que recuperar algo de ahi.
            moved_aside = backup_dir / f"{label}-antes-de-restaurar-{stamp}"
            shutil.move(str(destination), str(moved_aside))
        shutil.copytree(source_dir, destination)

    print(f"Restaurado: {settings.sqlite_db_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
