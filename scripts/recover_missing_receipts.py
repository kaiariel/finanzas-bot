from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finance_bot.config import Settings
from finance_bot.report import generate_report


def _candidate_dirs(settings: Settings) -> list[Path]:
    return [
        settings.data_dir / "receipts-recovery",
        settings.local_receipts_fallback_dir,
    ]


def main() -> None:
    settings = Settings.from_env()
    candidates = _candidate_dirs(settings)
    recovered: list[tuple[int, str]] = []

    with sqlite3.connect(settings.sqlite_db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT id, local_path
            FROM receipts
            WHERE status = 'missing'
            ORDER BY id ASC
            """
        ).fetchall()

        for row in rows:
            original = Path(row["local_path"])
            filename = original.name
            replacement: Path | None = None
            for base_dir in candidates:
                candidate = base_dir / filename
                if candidate.exists():
                    replacement = candidate
                    break
            if replacement is None:
                continue

            connection.execute(
                """
                UPDATE receipts
                SET local_path = ?, status = 'pending', review_notes = ?
                WHERE id = ?
                """,
                (
                    str(replacement),
                    "Ruta recuperada desde carpeta local de rescate",
                    row["id"],
                ),
            )
            connection.execute(
                """
                UPDATE transactions
                SET receipt_local_path = ?
                WHERE receipt_id = ?
                   OR receipt_local_path = ?
                """,
                (str(replacement), row["id"], str(original)),
            )
            recovered.append((int(row["id"]), str(replacement)))

        connection.commit()

    generate_report(settings)

    if not recovered:
        print("No se recupero ningun receipt missing.")
        print("Copia los archivos a data/receipts-recovery y vuelve a ejecutar este script.")
        return

    for receipt_id, path in recovered:
        print(f"#{receipt_id} recuperado -> {path}")


if __name__ == "__main__":
    main()
