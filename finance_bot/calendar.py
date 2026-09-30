"""Agenda familiar compartida por el panel y Telegram, sin efectos contables."""
from __future__ import annotations

import calendar as month_calendar
import json
import re
from datetime import date, datetime, timedelta

from finance_bot.db import FinanceDatabase

KINDS = {"reminder": "Recordatorio", "breakfast": "Desayuno", "meal": "Comida", "event": "Evento", "note": "Nota"}
RECURRENCES = {"once", "daily", "weekly", "monthly", "yearly"}
STATUSES = {"pending", "completed", "skipped"}
WEEKDAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def parse_date(value: object) -> date:
    text = str(value or "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise ValueError("Usa una fecha válida (AAAA-MM-DD).")
    return date.fromisoformat(text)


def month_bounds(month: str) -> tuple[date, date]:
    start = parse_date(month + "-01")
    return start, start.replace(day=month_calendar.monthrange(start.year, start.month)[1])


def date_label(day: date) -> str:
    return f"{WEEKDAYS[day.weekday()].capitalize()}, {day.day} de {MONTHS[day.month - 1]} de {day.year}"


def _text(value: object, limit: int, *, required: bool = False) -> str:
    if value is not None and not isinstance(value, str):
        raise ValueError("El texto del evento no es válido.")
    text = (value or "").strip()
    if (required and not text) or len(text) > limit:
        raise ValueError(f"Introduce un texto de hasta {limit} caracteres.")
    return text


def validate_event(payload: dict) -> dict:
    title = _text(payload.get("title"), 200, required=True)
    kind = str(payload.get("kind") or "reminder")
    recurrence = str(payload.get("recurrence") or "once")
    if kind not in KINDS or recurrence not in RECURRENCES:
        raise ValueError("Tipo o repetición no válidos.")
    start = parse_date(payload.get("startDate"))
    end_text = _text(payload.get("endDate"), 10)
    if end_text and parse_date(end_text) < start:
        raise ValueError("La fecha final debe ser igual o posterior al inicio.")
    event_time = _text(payload.get("time"), 5)
    if event_time and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", event_time):
        raise ValueError("La hora debe tener formato HH:MM.")
    weekdays = payload.get("weekdays", [])
    if not isinstance(weekdays, list) or any(type(d) is not int or d not in range(7) for d in weekdays):
        raise ValueError("Los días de la semana no son válidos.")
    if recurrence == "weekly" and not weekdays:
        raise ValueError("Elige al menos un día para la repetición semanal.")
    return {"title": title, "details": _text(payload.get("details"), 4000), "kind": kind,
            "person": _text(payload.get("person"), 100), "startDate": start.isoformat(),
            "endDate": end_text if recurrence != "once" else "", "time": event_time,
            "recurrence": recurrence, "weekdays": sorted(set(weekdays)) if recurrence == "weekly" else []}


def occurs_on(event: dict, day: date) -> bool:
    start = parse_date(event["startDate"])
    if day < start or (event["endDate"] and day > parse_date(event["endDate"])):
        return False
    rule = event["recurrence"]
    if rule == "once":
        return day == start
    if rule == "daily":
        return True
    if rule == "weekly":
        return day.weekday() in event["weekdays"]
    if rule == "monthly":
        return day.day == start.day
    return (day.month, day.day) == (start.month, start.day)


class CalendarStore:
    def __init__(self, db: FinanceDatabase):
        self.db = db

    @staticmethod
    def _event(row) -> dict:
        return {"id": row["id"], "title": row["title"], "details": row["details"],
                "kind": row["kind"], "person": row["person"], "startDate": row["start_date"],
                "endDate": row["end_date"], "time": row["event_time"], "recurrence": row["recurrence"],
                "weekdays": json.loads(row["weekdays"]), "active": bool(row["active"]),
                "sourceLabel": row["source_label"], "hasSource": bool(row["source_path"])}

    def list_events(self) -> list[dict]:
        with self.db._connect() as connection:
            return [self._event(row) for row in connection.execute("SELECT * FROM calendar_events WHERE active=1 ORDER BY start_date,id")]

    def get_event(self, event_id: int):
        with self.db._connect() as connection:
            return connection.execute("SELECT * FROM calendar_events WHERE id=?", (event_id,)).fetchone()

    def save_event(self, payload: dict, event_id: int | None = None, *, source_key=None, source_label="", source_path="") -> int:
        updating = event_id is not None
        event = validate_event(payload)
        values = [event[k] for k in ("title", "details", "kind", "person", "startDate", "endDate", "time", "recurrence")]
        values.append(json.dumps(event["weekdays"]))
        with self.db.atomic():
            with self.db._connect() as connection:
                if event_id is not None:
                    if not self.get_event(event_id):
                        raise KeyError("No existe ese evento.")
                    connection.execute("UPDATE calendar_events SET title=?,details=?,kind=?,person=?,start_date=?,end_date=?,event_time=?,recurrence=?,weekdays=?,updated_at=? WHERE id=?",
                                       (*values, self.db._now(), event_id))
                else:
                    # Importar de nuevo conserva los cambios hechos por la familia.
                    if source_key:
                        existing = connection.execute("SELECT id FROM calendar_events WHERE source_key=?", (source_key,)).fetchone()
                        if existing:
                            return existing["id"]
                    cursor = connection.execute("INSERT INTO calendar_events(title,details,kind,person,start_date,end_date,event_time,recurrence,weekdays,source_key,source_label,source_path,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                                (*values, source_key, source_label, source_path, self.db._now(), self.db._now()))
                    event_id = cursor.lastrowid
            self.db.log_audit("update" if updating else "create", "calendar_event", event_id, event["title"])
        return event_id

    def archive_event(self, event_id: int) -> None:
        with self.db.atomic():
            if not self.get_event(event_id):
                raise KeyError("No existe ese evento.")
            with self.db._connect() as connection:
                connection.execute("UPDATE calendar_events SET active=0,updated_at=? WHERE id=?", (self.db._now(), event_id))
            self.db.log_audit("archive", "calendar_event", event_id, "Serie retirada del calendario")

    def set_occurrence(self, event_id: int, day: str, *, status: str | None = None, overrides: dict | None = None) -> None:
        selected = parse_date(day)
        if status is not None and status not in STATUSES:
            raise ValueError("Estado no válido.")
        clean = None
        if overrides is not None:
            if not isinstance(overrides, dict) or set(overrides) - {"title", "details", "person", "time", "kind"}:
                raise ValueError("Solo puedes cambiar el texto, tipo, persona u hora de este día.")
            clean = {}
            for key, value in overrides.items():
                clean[key] = _text(value, {"title": 200, "details": 4000, "person": 100, "time": 5, "kind": 20}[key], required=key == "title")
            if "kind" in clean and clean["kind"] not in KINDS:
                raise ValueError("Tipo no válido.")
            if clean.get("time") and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", clean["time"]):
                raise ValueError("Hora no válida.")
        with self.db.atomic():
            row = self.get_event(event_id)
            if not row or not row["active"] or not occurs_on(self._event(row), selected):
                raise ValueError("El evento no tiene lugar en esa fecha.")
            with self.db._connect() as connection:
                before = connection.execute("SELECT * FROM calendar_occurrences WHERE event_id=? AND date=?", (event_id, day)).fetchone()
                saved_status = status if status is not None else before["status"] if before else "pending"
                saved_overrides = json.dumps(clean, ensure_ascii=False) if clean is not None else before["overrides"] if before else "{}"
                connection.execute("INSERT INTO calendar_occurrences(event_id,date,status,overrides,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(event_id,date) DO UPDATE SET status=excluded.status,overrides=excluded.overrides,updated_at=excluded.updated_at",
                                   (event_id, day, saved_status, saved_overrides, self.db._now()))
            self.db.log_audit("status", "calendar_event", event_id, f"{day}: {saved_status}")

    def occurrences(self, start: date, end: date, *, include_skipped: bool = False) -> list[dict]:
        if end < start or (end - start).days > 366:
            raise ValueError("Consulta un rango de hasta un año.")
        with self.db._connect() as connection:
            exceptions = {(row["event_id"], row["date"]): row for row in connection.execute("SELECT * FROM calendar_occurrences WHERE date BETWEEN ? AND ?", (start.isoformat(), end.isoformat()))}
        result = []
        for event in self.list_events():
            day = start
            while day <= end:
                if occurs_on(event, day):
                    key = day.isoformat()
                    exception = exceptions.get((event["id"], key))
                    status = exception["status"] if exception else "pending"
                    if include_skipped or status != "skipped":
                        occurrence = {**event, "date": key, "status": status}
                        if exception:
                            occurrence.update(json.loads(exception["overrides"]))
                        result.append(occurrence)
                day += timedelta(days=1)
        return sorted(result, key=lambda e: (e["date"], e["time"] or "99:99", {"breakfast": 0, "meal": 1, "note": 3}.get(e["kind"], 2), e["id"]))

    def payload(self, start: date, end: date, *, today: date | None = None) -> dict:
        today = today or datetime.now(self.db.timezone).date()
        return {"events": self.list_events(), "occurrences": self.occurrences(start, end, include_skipped=True),
                "start": start.isoformat(), "end": end.isoformat(), "today": self.occurrences(today, today), "todayDate": today.isoformat()}

    def today_text(self, day: date) -> str:
        lines = ["HOY · " + date_label(day)]
        items = self.occurrences(day, day)
        if not items:
            lines.append("No hay eventos ni recordatorios para hoy.")
        for item in items:
            label = KINDS[item["kind"]]
            prefix = (item["time"] + " · ") if item["time"] else ""
            suffix = " · completado" if item["status"] == "completed" else ""
            person = " · " + item["person"] if item["person"] else ""
            lines.append(f"\n{prefix}{label}{person}: {item['title']}{suffix}")
            if item["details"]:
                lines.append(item["details"])
        return "\n".join(lines)
