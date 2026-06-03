from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finance_bot.config import Settings
from finance_bot.formatting import format_month, parse_created_at


def _is_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _month_folder(root: Path, created_at: str) -> Path:
    date = parse_created_at(created_at)
    return root / f"{date:%Y-%m} {format_month(date)}"


def main() -> None:
    settings = Settings.from_env()
    root = settings.receipts_sync_dir
    root.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(settings.sqlite_db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT created_at, local_path AS path FROM receipts
            WHERE local_path IS NOT NULL
            UNION
            SELECT created_at, receipt_local_path AS path FROM transactions
            WHERE receipt_local_path IS NOT NULL AND receipt_local_path != ''
            ORDER BY created_at ASC
            """
        ).fetchall()

        moved: list[tuple[str, str]] = []
        for row in rows:
            source = Path(row["path"])
            if not source.exists() or not _is_inside(source, root):
                continue

            destination_dir = _month_folder(root, row["created_at"])
            destination = destination_dir / source.name
            if source.resolve() == destination.resolve():
                continue

            if not _is_inside(destination, root):
                raise RuntimeError(f"Destino fuera de la carpeta de tickets: {destination}")

            destination_dir.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                raise RuntimeError(f"Ya existe el archivo destino: {destination}")

            shutil.move(str(source), str(destination))
            old_path = str(source)
            new_path = str(destination)
            connection.execute(
                "UPDATE receipts SET local_path = ? WHERE local_path = ?",
                (new_path, old_path),
            )
            connection.execute(
                "UPDATE transactions SET receipt_local_path = ? WHERE receipt_local_path = ?",
                (new_path, old_path),
            )
            moved.append((old_path, new_path))

        connection.commit()

    if not moved:
        print("No habia tickets por mover.")
        return

    for old_path, new_path in moved:
        print(f"{old_path} -> {new_path}")


if __name__ == "__main__":
    main()
