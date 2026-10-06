from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.error import BadRequest, NetworkError, RetryAfter
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from finance_bot.config import Settings
from finance_bot.db import FinanceDatabase
from finance_bot.calendar import CalendarStore
from finance_bot.calendar_delivery import message_parts, send_daily_calendar
from finance_bot.formatting import format_month
from finance_bot.local_transcription import (
    LocalTranscriptionUnavailable,
    transcribe_voice_file,
)
from finance_bot.parser import DEFAULT_EXPENSE_CATEGORY, parse_transaction, parse_transactions
from finance_bot.report import generate_report
from finance_bot.summaries import build_summary, send_weekly_summary
from finance_bot.telegram_ui import (
    category_from_argument,
    category_keyboard,
    deleted_text,
    parse_callback,
    restore_keyboard,
    ticket_from_reply,
    ticket_prompt,
    ticket_prompt_markup,
    ticket_total_text,
    transaction_keyboard,
    transaction_text,
)


logger = logging.getLogger(__name__)

# Telegram solo guarda 24 h los mensajes que el bot no ha recogido: si el bot
# pasa mas tiempo apagado, los mas antiguos se pierden sin remedio.
TELEGRAM_UPDATE_RETENTION = timedelta(hours=24)
HEARTBEAT_INTERVAL_SECONDS = 60
# Al arrancar llegan de golpe todos los mensajes acumulados; el reporte HTML se
# regenera una sola vez al terminar la rafaga en lugar de una vez por mensaje.
REPORT_REFRESH_DELAY_SECONDS = 3
DOWNLOAD_ATTEMPTS = 4
# Un mensaje con muchas lineas se confirma una a una, pero sin inundar el chat.
MAX_CONFIRMATIONS_PER_MESSAGE = 6
AMOUNT_ONLY_NOTE_RE = re.compile(r"^[\d.,\s]+(?:€|eur|euros?)?$", re.IGNORECASE)


def _format_money(cents: int, currency: str = "EUR") -> str:
    symbol = "EUR" if currency == "EUR" else currency
    return f"{cents / 100:.2f} {symbol}"


def _confirmation_text(parsed_count: int = 1) -> str:
    if parsed_count <= 1:
        return "Enviado para registro."
    return f"Enviados {parsed_count} movimientos para registro."


def _refresh_report(settings: Settings) -> None:
    try:
        generate_report(settings)
    except Exception:
        logger.exception("No se pudo actualizar el reporte HTML")


def _safe_suffix(name: str | None, fallback: str) -> str:
    if not name:
        return fallback
    suffix = Path(name).suffix
    return suffix if suffix else fallback


def _message_datetime(update: Update, settings: Settings) -> datetime:
    """Momento en que se envio el mensaje, no en el que el bot lo procesa.

    Tras tener la compu apagada, los mensajes acumulados se procesan horas o dias
    despues; usar la hora actual los fecharia (y archivaria) en el dia equivocado.
    """
    tz = ZoneInfo(settings.timezone)
    message = update.effective_message
    sent_at = message.date if message else None
    if sent_at is None:
        return datetime.now(tz)
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=timezone.utc)
    return sent_at.astimezone(tz)


def _created_at(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _timestamp(moment: datetime) -> str:
    return moment.strftime("%Y%m%d-%H%M%S")


def _month_dir(base_dir: Path, moment: datetime) -> Path:
    folder = base_dir / f"{moment:%Y-%m} {format_month(moment)}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _retry_delay(exc: Exception, attempt: int) -> float:
    if isinstance(exc, RetryAfter):
        delay = exc.retry_after
        return delay.total_seconds() if isinstance(delay, timedelta) else float(delay)
    return float(2**attempt)


async def _download_file(context: ContextTypes.DEFAULT_TYPE, file_id: str, path: Path) -> None:
    """Descarga reintentando: un corte de red no debe perder el ticket."""
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            telegram_file = await context.bot.get_file(file_id)
            await telegram_file.download_to_drive(custom_path=path)
            return
        except (NetworkError, RetryAfter) as exc:
            if attempt == DOWNLOAD_ATTEMPTS:
                raise
            delay = _retry_delay(exc, attempt)
            logger.warning(
                "Fallo la descarga de %s (intento %s/%s): %s. Reintento en %.0fs",
                path.name,
                attempt,
                DOWNLOAD_ATTEMPTS,
                exc,
                delay,
            )
            await asyncio.sleep(delay)


def _user_metadata(update: Update, settings: Settings) -> dict[str, object | None]:
    user = update.effective_user
    if not user:
        return {
            "telegram_user_id": None,
            "telegram_username": None,
            "telegram_full_name": None,
        }
    alias = settings.telegram_user_aliases.get(user.id)
    return {
        "telegram_user_id": user.id,
        "telegram_username": user.username,
        "telegram_full_name": alias or user.full_name,
    }


def _already_stored(update: Update, db: FinanceDatabase) -> bool:
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return False
    if db.telegram_message_already_stored(user.id, message.message_id):
        logger.info(
            "Mensaje %s de %s ya registrado; se ignora la entrega repetida",
            message.message_id,
            user.id,
        )
        return True
    return False


def _request_report_refresh(context: ContextTypes.DEFAULT_TYPE) -> None:
    event = context.application.bot_data.get("report_refresh")
    if event is None:
        _refresh_report(context.application.bot_data["settings"])
        return
    event.set()


async def _is_allowed(update: Update, settings: Settings) -> bool:
    user = update.effective_user
    if not user:
        return False
    allowed_ids = settings.allowed_telegram_user_ids
    if allowed_ids is None or user.id in allowed_ids:
        return True

    if update.effective_message:
        await update.effective_message.reply_text(
            f"Usuario no autorizado. Tu Telegram user id es {user.id}."
        )
    return False


def _get_services(context: ContextTypes.DEFAULT_TYPE):
    return (
        context.application.bot_data["settings"],
        context.application.bot_data["db"],
    )


async def _confirm_transaction(message, db: FinanceDatabase, transaction_id: int, *, ticket_id: int | None = None,
                               ask_category: bool = False, hint: str | None = None) -> None:
    """Muestra lo que se guardo, con botones para corregirlo desde el movil.

    Si la categoria no esta clara (Sin clasificar, Bizum...) se abre directamente
    la lista de categorias: un toque y queda bien.
    """
    row = db.get_transaction(transaction_id)
    if row is None:
        return
    ask_category = ask_category or row["category"] == DEFAULT_EXPENSE_CATEGORY
    keyboard = category_keyboard(row) if ask_category else transaction_keyboard(row)
    await message.reply_text(transaction_text(row, hint=hint, ticket_id=ticket_id), reply_markup=keyboard)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings, _ = _get_services(context)
    if not await _is_allowed(update, settings):
        return

    user_id = update.effective_user.id if update.effective_user else "desconocido"
    await update.effective_message.reply_text(
        "Bot listo.\n"
        f"Tu ID: {user_id}\n"
        "Usa /ayuda para ver comandos."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings, _ = _get_services(context)
    if not await _is_allowed(update, settings):
        return

    await update.effective_message.reply_text(
        "Comandos:\n"
        "/resumen - cierre estimado del mes, sobre semanal y pendientes\n"
        "/deshacer - elimina tu último movimiento (se puede restaurar)\n"
        "/pendientes - tickets sin registrar\n"
        "/hoy - desayunos, menú y recordatorios de hoy\n"
        "/estado - totales del mes y del historial\n"
        "/exportar - genera y envia CSV\n"
        "/reporte - genera y envia el panel HTML\n\n"
        "Texto rapido:\n"
        "gasto 34,20 supermercado\n"
        "ingreso 250 venta bici\n"
        "+1200 nomina\n"
        "-9,99 spotify\n"
        "Cada registro se confirma con botones para cambiar tipo o categoría, o deshacer.\n\n"
        "Tickets:\n"
        "envía la foto y responde al aviso con el total y el comercio (21,40 Mercadona).\n"
        "También vale escribir: #12 21,40 Mercadona"
    )


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings, db = _get_services(context)
    if not await _is_allowed(update, settings):
        return
    # La agenda consulta el día actual en Madrid, incluso si el mensaje llegó tarde.
    day = datetime.now(db.timezone).date()
    text = CalendarStore(db).today_text(day)
    # Telegram limita cada mensaje a 4096 caracteres. Dividir conserva todos los datos.
    for part in message_parts(text):
        await update.effective_message.reply_text(part)


async def _register_ticket_total(update: Update, context: ContextTypes.DEFAULT_TYPE, receipt_id: int) -> None:
    """Total de un ticket enviado como respuesta: queda registrado con la fecha del ticket."""
    settings, db = _get_services(context)
    message = update.effective_message
    receipt = db.get_receipt(receipt_id)
    if receipt is None:
        await message.reply_text(f"No encuentro el ticket #{receipt_id}. Revisa el número en /pendientes.")
        return
    linked = db.receipt_transactions(receipt_id)
    if linked:
        await message.reply_text(
            f"El ticket #{receipt_id} ya está registrado (movimiento #{linked[0]['id']}). "
            "Usa Deshacer en su mensaje o corrígelo en el panel."
        )
        return
    if _already_stored(update, db):
        return
    text = ticket_total_text(message)
    parsed = parse_transaction(text)
    if parsed is None:
        await message.reply_text(f"No veo el total del ticket #{receipt_id}. Responde por ejemplo: 21,40 Mercadona")
        return
    parsed = db.apply_learned_category(parsed)
    note = parsed.note
    if AMOUNT_ONLY_NOTE_RE.match(note or ""):
        note = f"Ticket {parsed.store}".strip() if parsed.store else f"Ticket #{receipt_id}"
    transaction_id = db.register_receipt_total(
        receipt_id,
        amount_cents=parsed.amount_cents,
        category=parsed.category,
        note=note,
        kind=parsed.kind,
        store=parsed.store,
        source_text=text,
        inference_notes=parsed.inference_notes,
        telegram_message_id=message.message_id,
    )
    db.remember_telegram_message(update.effective_user.id if update.effective_user else None, message.message_id)
    _request_report_refresh(context)
    await _confirm_transaction(message, db, transaction_id, ticket_id=receipt_id)


async def record_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings, db = _get_services(context)
    if not await _is_allowed(update, settings):
        return

    message = update.effective_message
    text = message.text or ""
    if text.strip().casefold() == "hoy":
        await today_command(update, context)
        return
    receipt_id = ticket_from_reply(message)
    if receipt_id is not None:
        await _register_ticket_total(update, context, receipt_id)
        return
    parsed_transactions = parse_transactions(text)
    if not parsed_transactions:
        await message.reply_text(
            "No veo un importe claro. Prueba: gasto 12,50 mercadona comida"
        )
        return

    if _already_stored(update, db):
        return

    user_metadata = _user_metadata(update, settings)
    created_at = _created_at(_message_datetime(update, settings))
    ids = db.add_telegram_transactions(parsed_transactions,
        telegram_message_id=message.message_id,
        created_at=created_at, **user_metadata)
    if not ids:
        return
    _request_report_refresh(context)
    if len(ids) > MAX_CONFIRMATIONS_PER_MESSAGE:
        await message.reply_text(
            f"Registrados {len(ids)} movimientos (#{ids[0]} a #{ids[-1]}). "
            "Revísalos en el panel o usa /deshacer para quitar el último."
        )
        return
    for parsed, transaction_id in zip(parsed_transactions, ids):
        hint = None
        if parsed.needs_clarification:
            hint = "❓ Bizum o transferencia: ¿es ingreso, deuda, ayuda familiar, ahorro u otra categoría? Elige abajo."
        await _confirm_transaction(message, db, transaction_id, ask_category=parsed.needs_clarification, hint=hint)


async def record_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings, db = _get_services(context)
    if not await _is_allowed(update, settings):
        return

    voice = update.effective_message.voice
    if not voice or _already_stored(update, db):
        return

    sent_at = _message_datetime(update, settings)
    created_at = _created_at(sent_at)
    local_path = settings.resolved_voices_dir() / (
        f"voice-{_timestamp(sent_at)}-{voice.file_unique_id}.ogg"
    )
    await _download_file(context, voice.file_id, local_path)

    if settings.voice_transcription_enabled:
        try:
            transcription = await asyncio.to_thread(transcribe_voice_file, local_path, settings)
        except LocalTranscriptionUnavailable as exc:
            logger.warning("Transcripcion local no disponible: %s", exc)
        except Exception:
            logger.exception("No se pudo transcribir la nota de voz")
        else:
            parsed_transactions = parse_transactions(transcription)
            if parsed_transactions and not any(
                transaction.needs_clarification for transaction in parsed_transactions
            ):
                user_metadata = _user_metadata(update, settings)
                ids = db.add_telegram_transactions(parsed_transactions,
                    receipt_local_path=str(local_path),
                    telegram_message_id=update.effective_message.message_id,
                    created_at=created_at, **user_metadata)
                if not ids:
                    return
                _request_report_refresh(context)
                for transaction_id in ids[:MAX_CONFIRMATIONS_PER_MESSAGE]:
                    await _confirm_transaction(
                        update.effective_message, db, transaction_id,
                        hint=f"🎙️ Entendí: «{transcription.strip()}»",
                    )
                return

            pending_id = db.add_receipt(
                local_path=str(local_path),
                drive_file_id=None,
                drive_url=None,
                telegram_message_id=update.effective_message.message_id,
                caption=f"transcripcion sin registrar: {transcription or 'sin texto'}",
                status="voice_pending",
                created_at=created_at,
                **_user_metadata(update, settings),
            )
            _request_report_refresh(context)
            await update.effective_message.reply_text(
                f"Voz recibida para revisión #{pending_id}. Entendí: «{(transcription or '').strip() or 'nada'}». "
                "Si es un gasto, escríbelo en texto: gasto 12,50 mercadona."
            )
            return

    pending_id = db.add_receipt(
        local_path=str(local_path),
        drive_file_id=None,
        drive_url=None,
        telegram_message_id=update.effective_message.message_id,
        caption="nota de voz pendiente",
        status="voice_pending",
        created_at=created_at,
        **_user_metadata(update, settings),
    )
    _request_report_refresh(context)
    await update.effective_message.reply_text(
        f"Voz recibida para revisión #{pending_id}."
    )


async def record_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings, db = _get_services(context)
    if not await _is_allowed(update, settings):
        return

    message = update.effective_message
    document = message.document
    photo = message.photo[-1] if message.photo else None

    if document:
        telegram_file_id = document.file_id
        unique_id = document.file_unique_id
        suffix = _safe_suffix(document.file_name, ".bin")
        label = "documento"
    elif photo:
        telegram_file_id = photo.file_id
        unique_id = photo.file_unique_id
        suffix = ".jpg"
        label = "foto"
    else:
        return

    if _already_stored(update, db):
        return

    sent_at = _message_datetime(update, settings)
    local_path = _month_dir(settings.resolved_receipts_dir(), sent_at) / (
        f"ticket-{_timestamp(sent_at)}-{unique_id}{suffix}"
    )
    await _download_file(context, telegram_file_id, local_path)

    caption = message.caption or ""
    receipt_id = db.add_receipt(
        local_path=str(local_path),
        drive_file_id=None,
        drive_url=None,
        telegram_message_id=message.message_id,
        caption=caption or None,
        created_at=_created_at(sent_at),
        **_user_metadata(update, settings),
    )
    _request_report_refresh(context)

    # Con el total en el pie de foto no hace falta preguntar: se registra ya.
    parsed = parse_transaction(caption) if caption.strip() else None
    if parsed is not None:
        parsed = db.apply_learned_category(parsed)
        note = parsed.note
        if AMOUNT_ONLY_NOTE_RE.match(note or ""):
            note = f"Ticket {parsed.store}".strip() if parsed.store else f"Ticket #{receipt_id}"
        transaction_id = db.register_receipt_total(
            receipt_id,
            amount_cents=parsed.amount_cents,
            category=parsed.category,
            note=note,
            kind=parsed.kind,
            store=parsed.store,
            source_text=caption,
            inference_notes=parsed.inference_notes,
        )
        _request_report_refresh(context)
        await _confirm_transaction(
            message, db, transaction_id, ticket_id=receipt_id,
            hint="Tomado del pie de foto. Si el total no es ese, pulsa Deshacer y responde con el correcto.",
        )
        return

    await message.reply_text(ticket_prompt(receipt_id, label), reply_markup=ticket_prompt_markup())


async def undo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Elimina el ultimo movimiento que esta persona registro desde Telegram (restaurable)."""
    settings, db = _get_services(context)
    if not await _is_allowed(update, settings):
        return
    user = update.effective_user
    row = db.last_telegram_transaction(user.id) if user else None
    if row is None:
        await update.effective_message.reply_text("No hay ningún movimiento tuyo que deshacer.")
        return
    db.delete_transaction(int(row["id"]))
    _request_report_refresh(context)
    await update.effective_message.reply_text(deleted_text(row), reply_markup=restore_keyboard(int(row["id"])))


async def transaction_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Botones bajo cada confirmacion: cambiar tipo, categoria, deshacer o restaurar."""
    settings, db = _get_services(context)
    query = update.callback_query
    user = update.effective_user
    allowed = settings.allowed_telegram_user_ids
    if not user or (allowed is not None and user.id not in allowed):
        await query.answer("Usuario no autorizado.", show_alert=True)
        return
    parsed = parse_callback(query.data or "")
    if parsed is None:
        await query.answer("Botón no reconocido.")
        return
    transaction_id, action, argument = parsed
    row = db.get_transaction(transaction_id)
    feedback = ""
    try:
        if action == "undo":
            if row is None:
                await query.answer("Ya estaba eliminado.")
                return
            db.delete_transaction(transaction_id)
            text, keyboard = deleted_text(row), restore_keyboard(transaction_id)
            feedback = "Movimiento eliminado."
        elif action == "restore":
            row = db.restore_deleted_transaction(transaction_id)
            text, keyboard = transaction_text(row), transaction_keyboard(row)
            feedback = "Movimiento restaurado."
        elif row is None:
            await query.answer("Este movimiento ya no existe.", show_alert=True)
            return
        elif action == "kind":
            new_kind = "income" if row["kind"] == "expense" else "expense"
            row = db.update_transaction_fields(transaction_id, kind=new_kind)
            text, keyboard = transaction_text(row), transaction_keyboard(row)
            feedback = "Ahora es un ingreso." if new_kind == "income" else "Ahora es un gasto."
        elif action == "cat":
            category = category_from_argument(argument)
            if category is None:
                await query.answer("Categoría no reconocida.")
                return
            row = db.update_transaction_fields(transaction_id, category=category)
            text, keyboard = transaction_text(row), transaction_keyboard(row)
            feedback = f"Categoría: {category}. La próxima vez se usará sola."
        elif action == "cats":
            text, keyboard = transaction_text(row, hint="🏷️ Elige la categoría:"), category_keyboard(row)
        elif action == "back":
            text, keyboard = transaction_text(row), transaction_keyboard(row)
        else:
            await query.answer("Botón no reconocido.")
            return
    except (KeyError, ValueError) as exc:
        await query.answer(str(exc), show_alert=True)
        return
    if action in {"undo", "restore", "kind", "cat"}:
        _request_report_refresh(context)
    await query.answer(feedback or None)
    try:
        await query.edit_message_text(text, reply_markup=keyboard)
    except BadRequest as exc:
        # "Message is not modified" cuando se pulsa dos veces lo mismo.
        logger.info("No se pudo actualizar el mensaje de confirmacion: %s", exc)


async def pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings, db = _get_services(context)
    if not await _is_allowed(update, settings):
        return

    rows = db.list_pending_files()
    if not rows:
        await update.effective_message.reply_text("No hay tickets ni voces pendientes.")
        return

    lines = ["Pendientes (responde al aviso del ticket o escribe «#ID total comercio»):"]
    for row in rows[:20]:
        user_id = row["telegram_user_id"]
        user_label = (
            settings.telegram_user_aliases.get(user_id)
            or row["telegram_full_name"]
            or row["telegram_username"]
            or "sin usuario"
        )
        lines.append(f"#{row['id']} · {row['created_at'][:10]} · {user_label} · {Path(row['local_path']).name}")
    if len(rows) > 20:
        lines.append(f"...y {len(rows) - 20} mas.")
    await update.effective_message.reply_text("\n".join(lines))


async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings, db = _get_services(context)
    if not await _is_allowed(update, settings):
        return

    text = await asyncio.to_thread(build_summary, settings)
    await update.effective_message.reply_text(text)


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings, db = _get_services(context)
    if not await _is_allowed(update, settings):
        return

    data = db.status_overview()
    month_balance = data["month_income_cents"] - data["month_expense_cents"]
    total_balance = data["total_income_cents"] - data["total_expense_cents"]
    await update.effective_message.reply_text(
        "Estado de finanzas:\n"
        f"Mes: ingresos {_format_money(data['month_income_cents'])}, gastos {_format_money(data['month_expense_cents'])}, balance {_format_money(month_balance)}.\n"
        f"Total: ingresos {_format_money(data['total_income_cents'])}, gastos {_format_money(data['total_expense_cents'])}, balance {_format_money(total_balance)}.\n"
        f"Pendientes: {data['pending_count']}."
    )


async def export_csv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings, db = _get_services(context)
    if not await _is_allowed(update, settings):
        return

    csv_path = db.export_csv(settings.export_csv_path)
    with csv_path.open("rb") as handle:
        await update.effective_message.reply_document(
            document=handle,
            filename=csv_path.name,
            caption="Export CSV de movimientos.",
        )


async def report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings, _ = _get_services(context)
    if not await _is_allowed(update, settings):
        return

    report_path = await asyncio.to_thread(generate_report, settings)
    with report_path.open("rb") as handle:
        await update.effective_message.reply_document(
            document=handle,
            filename=report_path.name,
            caption="Reporte HTML interactivo.",
        )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update is None and isinstance(context.error, NetworkError):
        # Cortes de red mientras escucha: la libreria reintenta sola.
        logger.warning("Sin conexion con Telegram: %s", context.error)
        return
    logger.exception("Error manejando update", exc_info=context.error)
    message = update.effective_message if isinstance(update, Update) else None
    if message is not None:
        try:
            await message.reply_text(
                "No pude guardar este mensaje por un error. Reenvialo en un rato."
            )
        except Exception:
            logger.warning("No se pudo avisar del error al usuario")


def _heartbeat_path(settings: Settings) -> Path:
    return settings.data_dir / "bot_heartbeat.json"


def _read_heartbeat(path: Path) -> datetime | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        last_seen = datetime.fromisoformat(payload["last_seen"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=timezone.utc)
    return last_seen


def _write_heartbeat(path: Path, moment: datetime) -> None:
    path.write_text(json.dumps({"last_seen": moment.isoformat()}), encoding="utf-8")


def _lost_messages_warning(last_seen: datetime, now: datetime, settings: Settings) -> str:
    tz = ZoneInfo(settings.timezone)
    offline_since = last_seen.astimezone(tz)
    kept_since = (now - TELEGRAM_UPDATE_RETENTION).astimezone(tz)
    return (
        f"Aviso: el bot estuvo apagado desde el {offline_since:%d/%m %H:%M}. "
        "Telegram solo guarda 24 h los mensajes pendientes, asi que lo enviado entre el "
        f"{offline_since:%d/%m %H:%M} y el {kept_since:%d/%m %H:%M} no llego. "
        "Si mandaste tickets o gastos en ese rango, reenvialos."
    )


async def _warn_lost_messages(
    application: Application, last_seen: datetime, now: datetime
) -> None:
    settings: Settings = application.bot_data["settings"]
    text = _lost_messages_warning(last_seen, now, settings)
    logger.warning(text)
    for user_id in sorted(settings.allowed_telegram_user_ids or ()):
        try:
            await application.bot.send_message(chat_id=user_id, text=text)
        except Exception:
            logger.warning("No se pudo enviar el aviso de mensajes perdidos a %s", user_id)


async def _heartbeat_loop(application: Application) -> None:
    """Registra que el bot esta vivo y avisa si estuvo caido mas de 24 h.

    Cubre tanto la compu apagada como la suspension: al despertar, el hueco
    entre latidos delata el tiempo sin recoger mensajes.
    """
    path = _heartbeat_path(application.bot_data["settings"])
    last_seen = _read_heartbeat(path)
    while True:
        now = datetime.now(timezone.utc)
        if last_seen is not None and now - last_seen > TELEGRAM_UPDATE_RETENTION:
            await _warn_lost_messages(application, last_seen, now)
        try:
            _write_heartbeat(path, now)
        except OSError as exc:
            logger.warning("No se pudo actualizar %s: %s", path, exc)
        last_seen = now
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)


async def _report_refresh_loop(application: Application) -> None:
    settings: Settings = application.bot_data["settings"]
    event: asyncio.Event = application.bot_data["report_refresh"]
    while True:
        await event.wait()
        await asyncio.sleep(REPORT_REFRESH_DELAY_SECONDS)
        event.clear()
        await asyncio.to_thread(_refresh_report, settings)


async def _calendar_summary_loop(application: Application) -> None:
    while True:
        try:
            await send_daily_calendar(application, datetime.now(timezone.utc))
        except Exception:
            logger.warning("No se pudo comprobar el resumen diario del calendario")
        await asyncio.sleep(30)


async def _weekly_summary_loop(application: Application) -> None:
    while True:
        try:
            await send_weekly_summary(application, datetime.now(timezone.utc))
        except Exception:
            logger.exception("No se pudo comprobar el resumen semanal")
        await asyncio.sleep(60)


async def _post_init(application: Application) -> None:
    settings: Settings = application.bot_data["settings"]
    if settings.calendar_daily_summary_enabled:
        logger.info("Resumen diario del calendario activo a las 07:00 (%s), solo con eventos", settings.timezone)
    if settings.weekly_summary_enabled:
        logger.info("Resumen semanal de finanzas activo: domingos a partir de las 19:00 (%s)", settings.timezone)
    application.bot_data["report_refresh"] = asyncio.Event()
    application.bot_data["background_tasks"] = [
        asyncio.create_task(_heartbeat_loop(application)),
        asyncio.create_task(_report_refresh_loop(application)),
        asyncio.create_task(_calendar_summary_loop(application)),
        asyncio.create_task(_weekly_summary_loop(application)),
    ]


async def _post_stop(application: Application) -> None:
    for task in application.bot_data.pop("background_tasks", []):
        task.cancel()
    event = application.bot_data.get("report_refresh")
    if event is not None and event.is_set():
        _refresh_report(application.bot_data["settings"])


def build_application(settings: Settings) -> Application:
    settings.validate_for_bot()
    settings.ensure_dirs()

    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone)

    application = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .post_init(_post_init)
        .post_stop(_post_stop)
        .build()
    )
    application.bot_data["settings"] = settings
    application.bot_data["db"] = db

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("ayuda", help_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("estado", status))
    application.add_handler(CommandHandler("resumen", summary))
    application.add_handler(CommandHandler("exportar", export_csv))
    application.add_handler(CommandHandler("reporte", report))
    application.add_handler(CommandHandler("pendientes", pending))
    application.add_handler(CommandHandler("hoy", today_command))
    application.add_handler(CommandHandler("deshacer", undo_command))
    application.add_handler(CallbackQueryHandler(transaction_button, pattern=r"^tx:"))
    # Solo mensajes nuevos: editar un mensaje ya enviado no debe registrarlo otra vez.
    new_messages = filters.UpdateType.MESSAGE
    application.add_handler(MessageHandler(new_messages & filters.VOICE, record_voice))
    application.add_handler(
        MessageHandler(new_messages & (filters.PHOTO | filters.Document.ALL), record_receipt)
    )
    application.add_handler(
        MessageHandler(new_messages & filters.TEXT & ~filters.COMMAND, record_text)
    )
    application.add_error_handler(error_handler)
    return application


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=logging.INFO,
    )
    # httpx registra cada peticion con la URL completa, que incluye el token del
    # bot: llenaba bot.log (sincronizado por OneDrive) con el token en claro.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = Settings.from_env()
    application = build_application(settings)
    # bootstrap_retries=-1: si al encender la compu aun no hay red, el bot espera
    # y reintenta en lugar de cerrarse ("Failed run number 0 of 0. Aborting").
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        bootstrap_retries=-1,
        drop_pending_updates=False,
    )
