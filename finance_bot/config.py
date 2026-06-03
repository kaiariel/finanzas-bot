from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _parse_allowed_user_ids(value: str | None) -> set[int] | None:
    if not value:
        return None

    user_ids: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        user_ids.add(int(item))
    return user_ids or None


def _parse_user_aliases(value: str | None) -> dict[int, str]:
    if not value:
        return {}

    aliases: dict[int, str] = {}
    for item in value.split(","):
        item = item.strip()
        if not item or ":" not in item:
            continue
        user_id, name = item.split(":", 1)
        user_id = user_id.strip()
        name = name.strip()
        if user_id and name:
            aliases[int(user_id)] = name
    return aliases


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "si", "sí", "on"}


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    allowed_telegram_user_ids: set[int] | None
    telegram_user_aliases: dict[int, str]
    data_dir: Path
    sqlite_db_path: Path
    export_csv_path: Path
    report_html_path: Path
    receipts_sync_dir: Path
    voices_sync_dir: Path
    timezone: str
    voice_transcription_enabled: bool
    voice_transcription_model: str
    voice_transcription_device: str
    voice_transcription_compute_type: str

    @classmethod
    def from_env(cls, env_file: str | Path = ".env") -> "Settings":
        load_dotenv(env_file, encoding="utf-8-sig")

        data_dir = Path(os.getenv("DATA_DIR", "data"))
        return cls(
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
            allowed_telegram_user_ids=_parse_allowed_user_ids(
                os.getenv("ALLOWED_TELEGRAM_USER_IDS")
            ),
            telegram_user_aliases=_parse_user_aliases(os.getenv("TELEGRAM_USER_NAMES")),
            data_dir=data_dir,
            sqlite_db_path=Path(os.getenv("SQLITE_DB_PATH", data_dir / "finances.db")),
            export_csv_path=Path(os.getenv("EXPORT_CSV_PATH", data_dir / "movimientos.csv")),
            report_html_path=Path(os.getenv("REPORT_HTML_PATH", "reports/finanzas.html")),
            receipts_sync_dir=Path(os.getenv("RECEIPTS_SYNC_DIR", data_dir / "receipts")),
            voices_sync_dir=Path(os.getenv("VOICES_SYNC_DIR", data_dir / "voices")),
            timezone=os.getenv("TIMEZONE", "Europe/Madrid").strip(),
            voice_transcription_enabled=_parse_bool(
                os.getenv("VOICE_TRANSCRIPTION_ENABLED"), default=False
            ),
            voice_transcription_model=os.getenv("VOICE_TRANSCRIPTION_MODEL", "base").strip(),
            voice_transcription_device=os.getenv("VOICE_TRANSCRIPTION_DEVICE", "cpu").strip(),
            voice_transcription_compute_type=os.getenv(
                "VOICE_TRANSCRIPTION_COMPUTE_TYPE", "int8"
            ).strip(),
        )

    def validate_for_bot(self) -> None:
        if not self.telegram_bot_token:
            raise RuntimeError("Falta TELEGRAM_BOT_TOKEN en .env")

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.sqlite_db_path.parent.mkdir(parents=True, exist_ok=True)
        self.export_csv_path.parent.mkdir(parents=True, exist_ok=True)
        self.report_html_path.parent.mkdir(parents=True, exist_ok=True)
        self.receipts_sync_dir.mkdir(parents=True, exist_ok=True)
        self.voices_sync_dir.mkdir(parents=True, exist_ok=True)
