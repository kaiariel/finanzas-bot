"""Read-only health checks and verified, portable SQLite backups."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import uuid


class DatabaseUnavailableError(RuntimeError):
    pass


def inspect_database(path: Path, *, check_integrity: bool = False) -> dict:
    path = path.resolve()
    if not path.is_file():
        raise DatabaseUnavailableError(
            f"No se encuentra tu base de finanzas: {path}. Recupera la base existente; "
            "no se ha creado una base vacía. Para una instalación nueva usa scripts/init_finances.py."
        )
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)) as connection:
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"transactions", "receipts", "projection_templates"}.issubset(tables):
                raise DatabaseUnavailableError("El archivo no contiene una base de Finanzas válida.")
            counts = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                      for table in ("transactions", "receipts", "projection_templates")}
            initialized = "finance_meta" in tables and connection.execute(
                "SELECT value FROM finance_meta WHERE key='initialized'"
            ).fetchone()
            if not any(counts.values()) and not initialized:
                raise DatabaseUnavailableError(
                    "La base está vacía y no corresponde a una instalación nueva confirmada. "
                    "Revisa la ruta y recupera tu historial antes de continuar."
                )
            if check_integrity and connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise DatabaseUnavailableError("La base no supera la comprobación de integridad.")
            latest = [row[0] for row in connection.execute(
                "SELECT MAX(created_at) FROM transactions UNION ALL SELECT MAX(created_at) FROM receipts"
            ) if row[0]]
            return {"path": str(path), "counts": counts, "lastRecordAt": max(latest, default=None)}
    except sqlite3.Error as exc:
        raise DatabaseUnavailableError(f"No se puede abrir la base de finanzas: {path}. {exc}") from exc


def read_backup_status(data_dir: Path) -> dict:
    try:
        result = json.loads((data_dir / "last_backup.json").read_text(encoding="utf-8"))
        if not isinstance(result, dict) or not Path(result.get("database", "")).is_file():
            return {}
        return result
    except (OSError, ValueError, TypeError):
        return {}


def create_backup(settings, *, include_media: bool = True) -> dict:
    inspect_database(settings.sqlite_db_path, check_integrity=True)
    directory = settings.data_dir / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    destination = directory / f"finances-{stamp}.db"
    temporary = destination.with_suffix(".partial")
    with closing(sqlite3.connect(settings.sqlite_db_path.resolve().as_uri() + "?mode=ro", uri=True)) as source:
        with closing(sqlite3.connect(temporary)) as target:
            source.backup(target)
            target.execute("PRAGMA journal_mode=DELETE")
    health = inspect_database(temporary, check_integrity=True)
    temporary.replace(destination)
    media = []
    if include_media:
        roots = [("receipts", settings.receipts_sync_dir), ("voices", settings.voices_sync_dir),
                 ("receipts-local", settings.local_receipts_fallback_dir),
                 ("voices-local", settings.local_voices_fallback_dir)]
        seen = set()
        for label, root in roots:
            if root.resolve() in seen:
                continue
            seen.add(root.resolve())
            if not root.is_dir():
                continue
            copied = directory / f"{label}-{stamp}"
            shutil.copytree(root, copied)
            media.append({"source": str(root.resolve()), "backup": str(copied.resolve())})
    result = {"verifiedAt": datetime.now(timezone.utc).isoformat(), "database": str(destination.resolve()),
              "source": str(settings.sqlite_db_path.resolve()), "counts": health["counts"],
              "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(), "media": media}
    # Publish the completion marker only after the database and media were copied.
    (directory / f"manifest-{stamp}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    marker = settings.data_dir / f".backup-{uuid.uuid4().hex}.tmp"
    marker.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    marker.replace(settings.data_dir / "last_backup.json")
    return result


def ensure_recent_backup(settings) -> dict:
    last = read_backup_status(settings.data_dir)
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(last["verifiedAt"])
        if age.total_seconds() < 86400 and last.get("source") == str(settings.sqlite_db_path.resolve()):
            return last
    except (KeyError, ValueError, TypeError):
        pass
    return create_backup(settings)
