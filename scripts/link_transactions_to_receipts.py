from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finance_bot.config import Settings
from finance_bot.db import FinanceDatabase


def main() -> None:
    settings = Settings.from_env()
    FinanceDatabase(settings.sqlite_db_path, settings.timezone)

    with sqlite3.connect(settings.sqlite_db_path) as connection:
        connection.row_factory = sqlite3.Row
        orphan_paths = connection.execute(
            """
            SELECT MIN(created_at) AS created_at,
                   receipt_local_path,
                   MAX(telegram_message_id) AS telegram_message_id,
                   MAX(telegram_user_id) AS telegram_user_id,
                   MAX(telegram_username) AS telegram_username,
                   MAX(telegram_full_name) AS telegram_full_name
            FROM transactions
            WHERE receipt_local_path IS NOT NULL
              AND receipt_local_path != ''
              AND NOT EXISTS (
                SELECT 1 FROM receipts
                WHERE receipts.local_path = transactions.receipt_local_path
              )
            GROUP BY receipt_local_path
            """
        ).fetchall()
        for row in orphan_paths:
            connection.execute(
                """
                INSERT INTO receipts (
                    created_at, local_path, drive_file_id, drive_url,
                    telegram_message_id, caption, status, telegram_user_id,
                    telegram_username, telegram_full_name, review_notes
                )
                VALUES (?, ?, NULL, NULL, ?, ?, 'processed', ?, ?, ?, ?)
                """,
                (
                    row["created_at"],
                    row["receipt_local_path"],
                    row["telegram_message_id"],
                    "archivo enlazado desde movimiento existente",
                    row["telegram_user_id"],
                    row["telegram_username"],
                    row["telegram_full_name"],
                    "Creado por link_transactions_to_receipts.py",
                ),
            )

        connection.execute(
            """
            UPDATE transactions
            SET receipt_id = (
                SELECT receipts.id
                FROM receipts
                WHERE receipts.local_path = transactions.receipt_local_path
                LIMIT 1
            )
            WHERE receipt_id IS NULL
              AND receipt_local_path IS NOT NULL
              AND receipt_local_path != ''
            """
        )
        changed = connection.total_changes
        connection.commit()

    print(f"Movimientos enlazados: {changed}")


if __name__ == "__main__":
    main()
