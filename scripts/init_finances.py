"""Explicit first installation; never replaces an existing database."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from finance_bot.config import Settings
from finance_bot.db import FinanceDatabase


def main():
    parser = argparse.ArgumentParser(description="Crear una base nueva, sin historial")
    parser.add_argument("--new", action="store_true", help="Confirma que se trata de una instalación nueva")
    args = parser.parse_args()
    settings = Settings.from_env()
    if not args.new:
        raise SystemExit("Para recuperar tu historial usa restore_finances.py. Para empezar de cero usa --new.")
    if settings.sqlite_db_path.exists():
        raise SystemExit("La base ya existe. No se modificó ningún dato.")
    FinanceDatabase(settings.sqlite_db_path, settings.timezone, create=True)
    print(f"Instalación nueva creada: {settings.sqlite_db_path}")


if __name__ == "__main__":
    main()
