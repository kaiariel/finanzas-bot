from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from finance_bot import bot
from finance_bot.config import Settings


def _settings(tmp_path) -> Settings:
    return Settings(
        telegram_bot_token="token",
        allowed_telegram_user_ids={1},
        telegram_user_aliases={},
        data_dir=tmp_path,
        sqlite_db_path=tmp_path / "finances.db",
        export_csv_path=tmp_path / "movimientos.csv",
        report_html_path=tmp_path / "finanzas.html",
        receipts_sync_dir=tmp_path / "receipts",
        voices_sync_dir=tmp_path / "voices",
        timezone="Europe/Madrid",
        prefer_codex_media_review=True,
        voice_transcription_enabled=False,
        voice_transcription_model="base",
        voice_transcription_device="cpu",
        voice_transcription_compute_type="int8",
    )


def test_message_datetime_uses_send_time_in_local_timezone(tmp_path) -> None:
    sent = datetime(2026, 8, 31, 22, 30, tzinfo=timezone.utc)
    update = SimpleNamespace(effective_message=SimpleNamespace(date=sent))

    moment = bot._message_datetime(update, _settings(tmp_path))

    # Enviado el 31/08 a las 00:30 hora de Madrid del 1/09: va a septiembre.
    assert moment.isoformat() == "2026-09-01T00:30:00+02:00"
    assert bot._timestamp(moment) == "20260901-003000"
    assert bot._month_dir(tmp_path, moment).name.startswith("2026-09")


def test_heartbeat_round_trip(tmp_path) -> None:
    path = tmp_path / "bot_heartbeat.json"
    moment = datetime(2026, 9, 19, 10, 0, tzinfo=timezone.utc)

    assert bot._read_heartbeat(path) is None
    bot._write_heartbeat(path, moment)
    assert bot._read_heartbeat(path) == moment

    path.write_text("no es json", encoding="utf-8")
    assert bot._read_heartbeat(path) is None


def test_lost_messages_warning_names_the_lost_range(tmp_path) -> None:
    last_seen = datetime(2026, 9, 15, 17, 0, tzinfo=timezone.utc)
    now = last_seen + timedelta(days=3)

    text = bot._lost_messages_warning(last_seen, now, _settings(tmp_path))

    assert "15/09 19:00" in text
    assert "17/09 19:00" in text


def test_record_text_ignores_redelivered_message_and_keeps_send_date(tmp_path) -> None:
    import asyncio

    from finance_bot.db import FinanceDatabase

    settings = _settings(tmp_path)
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone, create=True)
    replies: list[str] = []

    async def reply_text(text: str) -> None:
        replies.append(text)

    message = SimpleNamespace(
        text="gasto 12,50 mercadona",
        message_id=77,
        date=datetime(2026, 9, 17, 8, 0, tzinfo=timezone.utc),
        reply_text=reply_text,
    )
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1, username="ariel", full_name="Ariel"),
        effective_message=message,
    )
    refresh_requests = asyncio.Event()
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data={"settings": settings, "db": db, "report_refresh": refresh_requests}
        )
    )

    asyncio.run(bot.record_text(update, context))
    asyncio.run(bot.record_text(update, context))

    rows = db.status_overview()
    assert rows["total_expense_cents"] == 1250
    assert replies == ["Enviado para registro."]
    assert refresh_requests.is_set()


def test_download_retries_telegram_retry_after(tmp_path, monkeypatch) -> None:
    import asyncio
    from telegram.error import RetryAfter

    attempts = {"count": 0}
    waits: list[float] = []

    class Download:
        async def download_to_drive(self, custom_path) -> None:
            Path(custom_path).write_bytes(b"ticket")

    class TelegramBot:
        async def get_file(self, _file_id):
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise RetryAfter(3)
            return Download()

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(bot.asyncio, "sleep", fake_sleep)
    path = tmp_path / "ticket.jpg"
    asyncio.run(bot._download_file(SimpleNamespace(bot=TelegramBot()), "abc", path))

    assert attempts["count"] == 2
    assert waits == [3.0]
    assert path.read_bytes() == b"ticket"
