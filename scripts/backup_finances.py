from pathlib import Path
import argparse
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from finance_bot.config import Settings
from finance_bot.storage import create_backup, ensure_recent_backup


def main():
    parser = argparse.ArgumentParser(description="Copia verificada de la base y los adjuntos")
    parser.add_argument("--if-due", action="store_true")
    args = parser.parse_args()
    settings = Settings.from_env()
    result = ensure_recent_backup(settings) if args.if_due else create_backup(settings)
    print(f"Copia verificada: {result['database']}")
    print(f"Registros: {result['counts']}")


if __name__ == "__main__":
    main()
