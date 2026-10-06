"""Resumen de la agenda a las 07:00, solo si hay eventos y destinatarios autorizados."""
from __future__ import annotations

import logging
from datetime import datetime

from finance_bot.calendar import CalendarStore

logger = logging.getLogger(__name__)


def message_parts(text: str) -> list[str]:
    parts = []
    while text:
        cut = min(len(text), 3800)
        if len(text) > cut:
            newline = text.rfind("\n", 0, cut)
            if newline > 0:
                cut = newline
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return parts


async def send_daily_calendar(application, now: datetime) -> None:
    settings = application.bot_data["settings"]
    db = application.bot_data["db"]
    local = now.astimezone(db.timezone)
    # Se comprueba cada 30 s. Hay cinco minutos para reintentar un corte breve;
    # arrancar más tarde no envía resúmenes atrasados.
    if not settings.calendar_daily_summary_enabled or local.hour != 7 or local.minute >= 5:
        return
    recipients = sorted(settings.allowed_telegram_user_ids or ())
    if not recipients:
        return
    day = local.date()
    store = CalendarStore(db)
    items = store.occurrences(day, day)
    if not items:
        return
    for chat_id in recipients:
        try:
            with db._connect() as connection:
                saved = connection.execute("SELECT content,next_part FROM calendar_daily_deliveries WHERE day=? AND chat_id=?", (day.isoformat(), chat_id)).fetchone()
                if saved is None:
                    if not items:
                        continue
                    content = store.today_text(day)
                    connection.execute("INSERT INTO calendar_daily_deliveries(day,chat_id,content) VALUES(?,?,?)", (day.isoformat(), chat_id, content))
                    next_part = 0
                else:
                    content, next_part = saved["content"], saved["next_part"]
            parts = message_parts(content)
            for index in range(next_part, len(parts)):
                await application.bot.send_message(chat_id=chat_id, text=parts[index])
                with db._connect() as connection:
                    connection.execute("UPDATE calendar_daily_deliveries SET next_part=? WHERE day=? AND chat_id=?", (index + 1, day.isoformat(), chat_id))
        except Exception:
            # Un destinatario inaccesible no impide avisar a los demás. El estado
            # confirmado permite continuar después de un reinicio del bot.
            logger.warning("No se pudo completar el resumen diario para %s", chat_id)
