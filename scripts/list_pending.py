from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finance_bot.config import Settings
from finance_bot.db import FinanceDatabase


def main() -> None:
    settings = Settings.from_env()
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone)
    rows = db.list_pending_files()

    if not rows:
        print("No hay tickets ni voces pendientes.")
        return

    for row in rows:
        user = row["telegram_full_name"] or row["telegram_username"] or "sin usuario"
        print(f"#{row['id']} [{row['status']}] {row['created_at']} {user}")
        print(f"  archivo: {row['local_path']}")
        if row["caption"]:
            print(f"  caption: {row['caption']}")
        if row["review_notes"]:
            print(f"  nota: {row['review_notes']}")


if __name__ == "__main__":
    main()
