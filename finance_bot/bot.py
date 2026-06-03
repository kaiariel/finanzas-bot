from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from finance_bot.config import Settings
from finance_bot.db import FinanceDatabase
from finance_bot.formatting import format_month
from finance_bot.local_transcription import (
    LocalTranscriptionUnavailable,
    transcribe_voice_file,
)
from finance_bot.parser import parse_transactions
from finance_bot.report import generate_report


logger = logging.getLogger(__name__)


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


def _timestamp(settings: Settings) -> str:
    return datetime.now(ZoneInfo(settings.timezone)).strftime("%Y%m%d-%H%M%S")


def _month_dir(base_dir: Path, settings: Settings) -> Path:
    now = datetime.now(ZoneInfo(settings.timezone))
    folder = base_dir / f"{now:%Y-%m} {format_month(now)}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


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
        "/estado - estado global de las finanzas\n"
        "/resumen - resumen del mes actual\n"
        "/exportar - genera y envia CSV\n"
        "/reporte - genera y envia HTML interactivo\n"
        "/pendientes - lista tickets y voces pendientes\n\n"
        "Texto rapido:\n"
        "gasto 34,20 supermercado\n"
        "ingreso 250 venta bici\n"
        "+1200 nomina\n"
        "-9,99 spotify\n\n"
        "Tickets:\n"
        "envia una foto o PDF y quedara pendiente para analizarlo en Codex"
    )


async def record_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings, db = _get_services(context)
    if not await _is_allowed(update, settings):
        return

    text = update.effective_message.text or ""
    parsed_transactions = parse_transactions(text)
    if not parsed_transactions:
        await update.effective_message.reply_text(
            "No veo un importe claro. Prueba: gasto 12,50 mercadona comida"
        )
        return

    if any(transaction.needs_clarification for transaction in parsed_transactions):
        await update.effective_message.reply_text(
            "Â¿La transferencia/Bizum corresponde a ingreso, deuda, ayuda familiar, ahorro u otra categorÃ­a?"
        )
        return

    user_metadata = _user_metadata(update, settings)
    for parsed in parsed_transactions:
        db.add_transaction(
            parsed,
            telegram_message_id=update.effective_message.message_id,
            **user_metadata,
        )
    _refresh_report(settings)
    await update.effective_message.reply_text(_confirmation_text(len(parsed_transactions)))


async def record_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings, db = _get_services(context)
    if not await _is_allowed(update, settings):
        return

    voice = update.effective_message.voice
    if not voice:
        return

    local_path = settings.voices_sync_dir / f"voice-{_timestamp(settings)}-{voice.file_unique_id}.ogg"
    telegram_file = await context.bot.get_file(voice.file_id)
    await telegram_file.download_to_drive(custom_path=local_path)

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
                for parsed in parsed_transactions:
                    db.add_transaction(
                        parsed,
                        receipt_local_path=str(local_path),
                        telegram_message_id=update.effective_message.message_id,
                        **user_metadata,
                    )
                _refresh_report(settings)
                await update.effective_message.reply_text(
                    _confirmation_text(len(parsed_transactions))
                )
                return

            pending_id = db.add_receipt(
                local_path=str(local_path),
                drive_file_id=None,
                drive_url=None,
                telegram_message_id=update.effective_message.message_id,
                caption=f"transcripcion sin registrar: {transcription or 'sin texto'}",
                status="voice_pending",
                **_user_metadata(update, settings),
            )
            _refresh_report(settings)
            await update.effective_message.reply_text(
                f"Voz recibida para revisiÃ³n #{pending_id}."
            )
            return

    pending_id = db.add_receipt(
        local_path=str(local_path),
        drive_file_id=None,
        drive_url=None,
        telegram_message_id=update.effective_message.message_id,
        caption="nota de voz pendiente",
        status="voice_pending",
        **_user_metadata(update, settings),
    )
    _refresh_report(settings)
    await update.effective_message.reply_text(
        f"Voz recibida para revisiÃ³n #{pending_id}."
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
    elif photo:
        telegram_file_id = photo.file_id
        unique_id = photo.file_unique_id
        suffix = ".jpg"
    else:
        return

    local_path = _month_dir(settings.receipts_sync_dir, settings) / (
        f"ticket-{_timestamp(settings)}-{unique_id}{suffix}"
    )
    telegram_file = await context.bot.get_file(telegram_file_id)
    await telegram_file.download_to_drive(custom_path=local_path)

    caption = message.caption or ""
    receipt_id = db.add_receipt(
        local_path=str(local_path),
        drive_file_id=None,
        drive_url=None,
        telegram_message_id=message.message_id,
        caption=caption or None,
        **_user_metadata(update, settings),
    )
    _refresh_report(settings)
    await message.reply_text(
        f"Documento recibido para análisis #{receipt_id}."
    )


async def pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings, db = _get_services(context)
    if not await _is_allowed(update, settings):
        return

    rows = db.list_pending_files()
    if not rows:
        await update.effective_message.reply_text("No hay tickets ni voces pendientes.")
        return

    lines = ["Pendientes:"]
    for row in rows[:20]:
        user_id = row["telegram_user_id"]
        user_label = (
            settings.telegram_user_aliases.get(user_id)
            or row["telegram_full_name"]
            or row["telegram_username"]
            or "sin usuario"
        )
        lines.append(f"#{row['id']} [{row['status']}] {user_label}: {row['local_path']}")
    if len(rows) > 20:
        lines.append(f"...y {len(rows) - 20} mas.")
    await update.effective_message.reply_text("\n".join(lines))


async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings, db = _get_services(context)
    if not await _is_allowed(update, settings):
        return

    data = db.summary_current_month()
    balance = data["income_cents"] - data["expense_cents"]
    await update.effective_message.reply_text(
        "Resumen del mes:\n"
        f"Ingresos: {_format_money(data['income_cents'])} ({data['income_count']})\n"
        f"Gastos: {_format_money(data['expense_cents'])} ({data['expense_count']})\n"
        f"Balance: {_format_money(balance)}"
    )


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
    await update.effective_message.reply_document(
        document=csv_path.open("rb"),
        filename=csv_path.name,
        caption="Export CSV de movimientos.",
    )


async def report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings, _ = _get_services(context)
    if not await _is_allowed(update, settings):
        return

    report_path = generate_report(settings)
    await update.effective_message.reply_document(
        document=report_path.open("rb"),
        filename=report_path.name,
        caption="Reporte HTML interactivo.",
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Error manejando update", exc_info=context.error)


def build_application(settings: Settings) -> Application:
    settings.validate_for_bot()
    settings.ensure_dirs()

    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone)

    application = Application.builder().token(settings.telegram_bot_token).build()
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
    application.add_handler(MessageHandler(filters.VOICE, record_voice))
    application.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, record_receipt))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, record_text))
    application.add_error_handler(error_handler)
    return application


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=logging.INFO,
    )
    settings = Settings.from_env()
    application = build_application(settings)
    application.run_polling(allowed_updates=Update.ALL_TYPES)

