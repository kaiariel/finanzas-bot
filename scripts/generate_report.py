from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finance_bot.config import Settings
from finance_bot.report import generate_report


def main() -> None:
    settings = Settings.from_env()
    output = generate_report(settings)
    print(output.resolve())


if __name__ == "__main__":
    main()

