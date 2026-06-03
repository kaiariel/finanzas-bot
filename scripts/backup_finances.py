from __future__ import annotations

import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finance_bot.config import Settings
from finance_bot.db import FinanceDatabase
from finance_bot.report import generate_report


def main() -> None:
    settings = Settings.from_env()
    settings.ensure_dirs()
    backup_dir = settings.data_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    db_backup = backup_dir / f"finances-{stamp}.db"
    csv_backup = backup_dir / f"movimientos-{stamp}.csv"
    html_backup = backup_dir / f"finanzas-{stamp}.html"

    with sqlite3.connect(settings.sqlite_db_path) as source:
        with sqlite3.connect(db_backup) as target:
            source.backup(target)

    FinanceDatabase(settings.sqlite_db_path, settings.timezone).export_csv(settings.export_csv_path)
    if settings.export_csv_path.exists():
        shutil.copy2(settings.export_csv_path, csv_backup)

    report_path = generate_report(settings)
    if report_path.exists():
        shutil.copy2(report_path, html_backup)

    print(db_backup.resolve())


if __name__ == "__main__":
    main()
