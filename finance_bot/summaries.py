"""Resumen del mes para Telegram: la misma cuenta que el panel, en cuatro líneas.

Lo usan `/resumen` y el envío semanal del domingo. No modifica datos.
"""
from __future__ import annotations

import logging
from datetime import datetime

from finance_bot.formatting import format_euro, format_month

logger = logging.getLogger(__name__)

WEEKLY_SUMMARY_KIND = "weekly_summary"
# Domingo a partir de las 19:00; si el equipo estaba apagado, el lunes a cualquier hora.
WEEKLY_SEND_HOUR = 19


def _plural(count: int, singular: str, plural: str) -> str:
    return f"{count} {singular if count == 1 else plural}"


def inbox_line(inbox: dict) -> str:
    parts = []
    if inbox.get("receipts"):
        parts.append(_plural(len(inbox["receipts"]), "ticket sin registrar", "tickets sin registrar"))
    if inbox.get("unclassified"):
        parts.append(_plural(len(inbox["unclassified"]), "movimiento sin categoría", "movimientos sin categoría"))
    if inbox.get("manualCompleted"):
        parts.append(_plural(len(inbox["manualCompleted"]), "concepto marcado sin movimiento", "conceptos marcados sin movimiento"))
    if inbox.get("duplicates"):
        parts.append(_plural(len(inbox["duplicates"]), "posible duplicado", "posibles duplicados"))
    if not parts:
        return "📬 Nada pendiente de revisar."
    return "📬 Para revisar: " + " · ".join(parts) + "\nAbre el panel → Para revisar."


def summary_text(data: dict, *, title: str | None = None) -> str:
    """Texto del resumen a partir de los datos del panel (report_data)."""
    month = str(data["today"])[:7]
    month_name = format_month(datetime.fromisoformat(month + "-01"))
    summary = next((row for row in data["projections"]["months"] if row["monthKey"] == month), None)
    lines = [title or f"📊 {month_name} {month[:4]}"]
    if summary is None:
        lines.append("Sin previsión para este mes. Añade cobros y pagos esperados en Proyección.")
    else:
        registered = summary["actualIncomeCents"] - summary["actualExpenseCents"]
        estimate = registered + summary["pendingIncomeCents"] - summary["pendingExpenseCents"]
        lines[0] += f" · cierre estimado {format_euro(estimate)}"
        lines.append(
            f"Registrado {format_euro(registered)} · por cobrar {format_euro(summary['pendingIncomeCents'])}"
            f" · por pagar {format_euro(summary['pendingExpenseCents'])}"
        )
    envelope = next(
        (row for row in data["projections"]["rows"] if row["month"] == month and row.get("tracksActualCategory")),
        None,
    )
    if envelope:
        if envelope.get("weeklyBudgetCents"):
            lines.append(
                f"🧺 {envelope['name']}: esta semana {format_euro(int(envelope['weekSpentCents'] or 0))}"
                f" de {format_euro(int(envelope['weeklyBudgetCents']))} · queda en el mes {format_euro(int(envelope['remainingBudgetCents']))}"
            )
        else:
            lines.append(
                f"🧺 {envelope['name']}: gastado {format_euro(int(envelope['actualSpentCents']))}"
                f" · queda {format_euro(int(envelope['remainingBudgetCents']))}"
            )
    lines.append(inbox_line(data.get("inbox") or {}))
    return "\n".join(lines)


def build_summary(settings, *, title: str | None = None) -> str:
    from finance_bot.report import report_data

    return summary_text(report_data(settings), title=title)


def weekly_period(local: datetime) -> str | None:
    """Clave de la semana si toca enviar el resumen; None fuera de la ventana."""
    if local.weekday() == 6 and local.hour >= WEEKLY_SEND_HOUR:
        iso = local.isocalendar()
    elif local.weekday() == 0:
        iso = local.isocalendar()
        # El lunes cierra la semana anterior (la del domingo).
        return f"{iso.year}-W{iso.week - 1:02d}" if iso.week > 1 else f"{iso.year - 1}-W52"
    else:
        return None
    return f"{iso.year}-W{iso.week:02d}"


async def send_weekly_summary(application, now: datetime) -> None:
    settings = application.bot_data["settings"]
    db = application.bot_data["db"]
    if not getattr(settings, "weekly_summary_enabled", True):
        return
    local = now.astimezone(db.timezone)
    period = weekly_period(local)
    if period is None:
        return
    recipients = [chat_id for chat_id in sorted(settings.allowed_telegram_user_ids or ())
                  if not db.telegram_delivery_sent(WEEKLY_SUMMARY_KIND, period, chat_id)]
    if not recipients:
        return
    import asyncio

    text = await asyncio.to_thread(build_summary, settings, title=f"📬 Resumen semanal · {local:%d/%m}")
    for chat_id in recipients:
        try:
            await application.bot.send_message(chat_id=chat_id, text=text)
            db.mark_telegram_delivery(WEEKLY_SUMMARY_KIND, period, chat_id)
        except Exception:
            logger.warning("No se pudo enviar el resumen semanal a %s", chat_id)
