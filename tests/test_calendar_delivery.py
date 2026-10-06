from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from finance_bot.calendar import CalendarStore
from finance_bot.calendar_delivery import message_parts, send_daily_calendar
from finance_bot.db import FinanceDatabase
from test_bot import _settings


@pytest.fixture
def app(tmp_path):
    settings = replace(_settings(tmp_path), calendar_daily_summary_enabled=True)
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone, create=True)
    sent = []
    async def send_message(**message):
        sent.append(message)
    return SimpleNamespace(bot_data={"settings": settings, "db": db}, bot=SimpleNamespace(send_message=send_message), sent=sent)


def moment(hour=7, minute=0, day=1):
    return datetime(2026, 10, day, hour, minute, tzinfo=ZoneInfo("Europe/Madrid"))


def add_event(app, **updates):
    return CalendarStore(app.bot_data["db"]).save_event({"title":"Menú", "details":"Lomo al horno\nPan y fruta", "kind":"meal", "startDate":"2026-10-01", "recurrence":"once", **updates})


def test_sends_full_details_at_seven_once_even_after_restart(app):
    add_event(app)
    asyncio.run(send_daily_calendar(app, moment().astimezone(ZoneInfo("UTC"))))
    assert len(app.sent) == 1
    assert app.sent[0]["chat_id"] == 1
    assert "Lomo al horno\nPan y fruta" in app.sent[0]["text"]
    original = app.bot_data["db"]
    app.bot_data["db"] = FinanceDatabase(original.db_path, "Europe/Madrid")
    asyncio.run(send_daily_calendar(app, moment(minute=1)))
    assert len(app.sent) == 1
    assert original.list_transactions() == []


@pytest.mark.parametrize("hour,minute", [(6,59),(7,5),(8,0),(19,0)])
def test_does_not_send_before_or_after_delivery_window(app,hour,minute):
    add_event(app)
    asyncio.run(send_daily_calendar(app, moment(hour,minute)))
    assert app.sent == []


def test_empty_or_skipped_day_is_silent(app):
    asyncio.run(send_daily_calendar(app, moment()))
    assert app.sent == []
    event_id = add_event(app)
    CalendarStore(app.bot_data["db"]).set_occurrence(event_id,"2026-10-01",status="skipped")
    asyncio.run(send_daily_calendar(app, moment()))
    assert app.sent == []


def test_disabled_or_no_authorized_users_is_silent(app):
    add_event(app)
    settings = app.bot_data["settings"]
    for replacement in [replace(settings, calendar_daily_summary_enabled=False),replace(settings, allowed_telegram_user_ids=None)]:
        app.bot_data["settings"] = replacement
        asyncio.run(send_daily_calendar(app,moment()))
    assert app.sent == []


def test_recurring_event_is_sent_on_each_day(app):
    add_event(app,recurrence="daily")
    asyncio.run(send_daily_calendar(app,moment()))
    asyncio.run(send_daily_calendar(app,moment(day=2)))
    assert len(app.sent) == 2
    assert "2 de octubre" in app.sent[1]["text"]


def test_failed_recipient_does_not_block_others_and_can_retry(app):
    add_event(app)
    app.bot_data["settings"] = replace(app.bot_data["settings"],allowed_telegram_user_ids={1,2})
    attempts = []
    async def send_message(**message):
        attempts.append(message["chat_id"])
        if message["chat_id"]==1 and len(attempts)==1:
            raise RuntimeError("Offline")
        app.sent.append(message)
    app.bot.send_message = send_message
    asyncio.run(send_daily_calendar(app,moment()))
    asyncio.run(send_daily_calendar(app,moment(minute=1)))
    assert attempts == [1,2,1]
    assert [m["chat_id"] for m in app.sent] == [2,1]


def test_long_messages_resume_from_last_confirmed_part(app):
    for i in range(3):
        add_event(app,title=f"Evento {i}",details="x"*3500)
    attempts = []
    async def send_message(**message):
        attempts.append(message["text"])
        if len(attempts)==2:
            raise RuntimeError("Offline")
        app.sent.append(message)
    app.bot.send_message = send_message
    asyncio.run(send_daily_calendar(app,moment()))
    asyncio.run(send_daily_calendar(app,moment(minute=1)))
    expected = message_parts(CalendarStore(app.bot_data["db"]).today_text(moment().date()))
    assert [m["text"] for m in app.sent] == expected
    assert all(len(part)<=3800 for part in expected)
