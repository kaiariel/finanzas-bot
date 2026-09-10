from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finance_bot.config import Settings
from finance_bot.db import FinanceDatabase


def main() -> int:
    settings = Settings.from_env()
    checks: list[dict[str, object]] = []
    checks.append({"name": "Python", "ok": sys.version_info >= (3, 11), "detail": sys.version.split()[0]})
    checks.append({"name": "Token de Telegram", "ok": bool(settings.telegram_bot_token), "detail": "configurado" if settings.telegram_bot_token else "falta en .env"})
    try:
        settings.ensure_dirs()
        db = FinanceDatabase(settings.sqlite_db_path, settings.timezone)
        with db._connect() as connection:
            integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
        checks.append({"name": "SQLite", "ok": integrity == "ok", "detail": integrity})
    except Exception as exc:
        checks.append({"name": "SQLite", "ok": False, "detail": str(exc)})
    checks.append({"name": "Git", "ok": shutil.which("git") is not None, "detail": "disponible" if shutil.which("git") else "no encontrado"})
    print(json.dumps({"ok": all(bool(item["ok"]) for item in checks), "checks": checks}, ensure_ascii=False, indent=2))
    return 0 if all(bool(item["ok"]) for item in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
