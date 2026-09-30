from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from finance_bot.calendar import CalendarStore, occurs_on, validate_event
from finance_bot.db import FinanceDatabase


@pytest.fixture
def store(tmp_path):
    return CalendarStore(FinanceDatabase(tmp_path / "calendar.db", "Europe/Madrid", create=True))


def event(**updates):
    return {"title": "Llevar fruta", "kind": "reminder", "startDate": "2026-10-01", "recurrence": "once", **updates}


def test_weekly_rules_respect_start_end_and_selected_weekdays(store):
    store.save_event(event(recurrence="weekly", weekdays=[0, 2], endDate="2026-10-15"))
    dates = [item["date"] for item in store.occurrences(date(2026, 9, 29), date(2026, 10, 31))]
    assert dates == ["2026-10-05", "2026-10-07", "2026-10-12", "2026-10-14"]
    assert store.occurrences(date(2026, 10, 3), date(2026, 10, 4)) == []


def test_missing_monthly_and_leap_year_dates_are_not_shifted():
    monthly = validate_event(event(startDate="2026-01-31", recurrence="monthly"))
    assert not occurs_on(monthly, date(2026, 2, 28))
    assert occurs_on(monthly, date(2026, 3, 31))
    yearly = validate_event(event(startDate="2024-02-29", recurrence="yearly"))
    assert not occurs_on(yearly, date(2025, 2, 28))
    assert occurs_on(yearly, date(2028, 2, 29))


def test_single_day_edits_completion_and_skip_preserve_the_series(store):
    id = store.save_event(event(recurrence="weekly", weekdays=[3]))
    store.set_occurrence(id, "2026-10-08", overrides={"title": "Llevar bocadillo"})
    store.set_occurrence(id, "2026-10-08", status="completed")
    store.set_occurrence(id, "2026-10-15", status="skipped")
    items = store.occurrences(date(2026, 10, 1), date(2026, 10, 31))
    assert [e["title"] for e in items] == ["Llevar fruta", "Llevar bocadillo", "Llevar fruta", "Llevar fruta"]
    assert items[1]["status"] == "completed"
    assert len(store.occurrences(date(2026, 10, 1), date(2026, 10, 31), include_skipped=True)) == 5
    store.set_occurrence(id, "2026-10-15", status="pending")
    assert len(store.occurrences(date(2026, 10, 1), date(2026, 10, 31))) == 5
    with pytest.raises(ValueError):
        store.set_occurrence(id, "2026-10-09", status="completed")
    assert store.db.list_transactions() == []


@pytest.mark.parametrize("updates", [
    {"startDate": "2026-02-30"}, {"time": "25:01"}, {"endDate": "2026-09-30"},
    {"recurrence": "weekly", "weekdays": []}, {"weekdays": [7]}, {"weekdays": [True]},
    {"title": ""}, {"kind": "invalid"}, {"details": "x" * 4001},
])
def test_invalid_events_do_not_write(store, updates):
    with pytest.raises(ValueError):
        store.save_event(event(**updates))
    assert store.list_events() == []


def test_import_is_idempotent_and_preserves_edits(store):
    from scripts.import_calendar import import_events
    entries = [event(sourceKey="breakfast-thursday", recurrence="weekly", weekdays=[3])]
    ids = import_events(store.db, entries)
    store.save_event(event(title="Título corregido", recurrence="weekly", weekdays=[3]), ids[0])
    assert import_events(store.db, entries) == ids
    assert len(store.list_events()) == 1
    assert store.list_events()[0]["title"] == "Título corregido"
    assert store.db.list_transactions() == []


def test_archive_removes_future_occurrences_without_deleting_other_events(store):
    id = store.save_event(event(recurrence="daily"))
    store.save_event(event(title="Otro evento"))
    store.archive_event(id)
    assert [e["title"] for e in store.occurrences(date(2026, 10, 1), date(2026, 10, 31))] == ["Otro evento"]
    assert store.get_event(id)["active"] == 0


def test_imported_nursery_menu_matches_dates_and_has_no_weekend_meals(store):
    # Menú de ejemplo para las pruebas: 1 de octubre de 2026 cae en jueves.
    store.save_event(event(title="Galletas o bizcochos", kind="breakfast", recurrence="weekly", weekdays=[3]))
    store.save_event(event(title="Menú", kind="meal", details="Paella de verduras\nLomo Sajonia al horno\nPan y fruta"))
    text = store.today_text(date(2026, 10, 1))
    assert "Jueves, 1 de octubre de 2026" in text
    assert "Galletas o bizcochos" in text and "Paella de verduras" in text
    assert "No hay eventos" in store.today_text(date(2026, 10, 3))


def test_today_command_and_plain_hoy_use_madrid_date_and_authorization(tmp_path, monkeypatch):
    from finance_bot import bot
    from test_bot import _settings
    settings = _settings(tmp_path)
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone, create=True)
    CalendarStore(db).save_event(event(title="Menú de hoy", details="Arroz y pollo"))
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            assert str(tz) == "Europe/Madrid"
            return cls(2026, 10, 1, 0, 30, tzinfo=tz)
    monkeypatch.setattr(bot, "datetime", Clock)
    replies = []
    async def reply_text(text):
        replies.append(text)
    update = SimpleNamespace(effective_user=SimpleNamespace(id=1), effective_message=SimpleNamespace(text="hoy", reply_text=reply_text))
    context = SimpleNamespace(application=SimpleNamespace(bot_data={"settings": settings, "db": db}))
    asyncio.run(bot.record_text(update, context))
    assert "Menú de hoy" in replies[0]
    assert "Arroz y pollo" in replies[0]
    assert db.list_transactions() == []
    update.effective_user.id = 2
    asyncio.run(bot.today_command(update, context))
    assert "no autorizado" in replies[-1]
    assert "Menú" not in replies[-1]


def test_calendar_http_crud_and_invalid_origin(tmp_path):
    from http.server import ThreadingHTTPServer
    from threading import Thread
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen
    from test_serve_dashboard import _load_serve_dashboard, _settings
    module = _load_serve_dashboard()
    settings = _settings(tmp_path)
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone, create=True)
    class Handler(module.DashboardHandler):
        pass
    Handler.settings = settings
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_port}"
    def request(path, payload=None, method=None, origin=base):
        body = json.dumps(payload).encode() if payload is not None else None
        req = Request(base + path, data=body, method=method, headers={"Content-Type": "application/json", "Origin": origin})
        with urlopen(req) as response:
            return json.load(response)
    try:
        request("/api/calendar/events", event(recurrence="weekly", weekdays=[3]))
        items = request("/api/calendar?month=2026-10")
        assert len(items["occurrences"]) == 5
        id = items["events"][0]["id"]
        request(f"/api/calendar/events/{id}/2026-10-08", {"status": "skipped"})
        assert request("/api/calendar?month=2026-10")["occurrences"][1]["status"] == "skipped"
        with pytest.raises(HTTPError) as rejected:
            request("/api/calendar/events", event(), origin="https://foreign.example")
        assert rejected.value.code == 403
        with pytest.raises(HTTPError) as invalid:
            request("/api/calendar?month=2026-19")
        assert invalid.value.code == 400
        request(f"/api/calendar/events/{id}", {}, "DELETE")
        assert request("/api/calendar?month=2026-10")["events"] == []
        assert db.list_transactions() == []
    finally:
        server.shutdown(); worker.join(); server.server_close()
