"""Confirmaciones con botones, deshacer, total de ticket y resumen semanal por Telegram."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from finance_bot import bot
from finance_bot.db import FinanceDatabase, SUMMARY_REVIEW_STATUS
from finance_bot.summaries import send_weekly_summary, weekly_period
from finance_bot.telegram_ui import parse_callback
from test_bot import _settings


class FakeMessage:
    def __init__(self, text=None, *, message_id=1, caption=None, reply_to=None, photo=None):
        self.text = text
        self.caption = caption
        self.message_id = message_id
        self.date = datetime(2026, 10, 4, 8, 0, tzinfo=timezone.utc)
        self.reply_to_message = reply_to
        self.photo = photo or []
        self.document = None
        self.voice = None
        self.replies: list[tuple[str, object]] = []

    async def reply_text(self, text, reply_markup=None):
        self.replies.append((text, reply_markup))
        return SimpleNamespace(text=text, message_id=self.message_id + 100)


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.answers = []
        self.edits = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append(text)

    async def edit_message_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))


@pytest.fixture
def env(tmp_path):
    settings = _settings(tmp_path)
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone, create=True)
    context = SimpleNamespace(application=SimpleNamespace(bot_data={"settings": settings, "db": db, "report_refresh": asyncio.Event()}))
    user = SimpleNamespace(id=1, username="ariel", full_name="Ariel")
    return SimpleNamespace(settings=settings, db=db, context=context, user=user)


def send_text(env, text, message_id=1, reply_to=None):
    message = FakeMessage(text, message_id=message_id, reply_to=reply_to)
    update = SimpleNamespace(effective_user=env.user, effective_message=message)
    asyncio.run(bot.record_text(update, env.context))
    return message


def press(env, data):
    query = FakeQuery(data)
    update = SimpleNamespace(effective_user=env.user, callback_query=query)
    asyncio.run(bot.transaction_button(update, env.context))
    return query


def button_data(markup):
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def test_income_word_in_plural_is_an_income_and_the_reply_shows_what_was_saved(env):
    message = send_text(env, "Ingresos trabajo de fotografía 150 euros")
    [(text, markup)] = message.replies
    assert "Ingreso · 150,00 € · Trabajos extra" in text
    assert "«trabajo de fotografía»" in text
    data = button_data(markup)
    assert any(item.endswith(":kind") for item in data)
    assert any(item.endswith(":undo") for item in data)
    [row] = env.db.list_transactions()
    assert row["kind"] == "income"


def test_unclear_category_opens_the_category_list_directly(env):
    message = send_text(env, "Desayuno fuera 11 euros")
    [(text, markup)] = message.replies
    assert "Sin clasificar" in text and "elige una" in text
    assert any(item.endswith(":cat:0") for item in button_data(markup))


def test_bizum_is_saved_and_asks_for_the_category_instead_of_discarding(env):
    message = send_text(env, "Bizum 20 Juan")
    [(text, markup)] = message.replies
    assert "Bizum o transferencia" in text
    assert len(env.db.list_transactions()) == 1
    assert any(item.endswith(":cat:7") for item in button_data(markup))


def test_buttons_toggle_kind_change_category_learn_and_undo(env):
    message = send_text(env, "gasto 30 venta ropa usada")
    [(_, markup)] = message.replies
    [transaction_id] = [row["id"] for row in env.db.list_transactions()]
    assert parse_callback(button_data(markup)[0]) == (transaction_id, "kind", None)

    query = press(env, f"tx:{transaction_id}:kind")
    assert env.db.get_transaction(transaction_id)["kind"] == "income"
    assert env.db.get_transaction(transaction_id)["category"] == "Trabajos extra"
    assert "Ingreso · 30,00 €" in query.edits[-1][0]

    press(env, f"tx:{transaction_id}:cats")
    query = press(env, f"tx:{transaction_id}:cat:14")  # Ingresos clientes
    row = env.db.get_transaction(transaction_id)
    assert row["category"] == "Ingresos clientes"
    assert row["review_status"] == "reviewed"
    assert env.db.learned_category("venta ropa usada", "income") == "Ingresos clientes"
    assert "Ingresos clientes" in query.edits[-1][0]

    query = press(env, f"tx:{transaction_id}:undo")
    assert env.db.get_transaction(transaction_id) is None
    assert "Eliminado" in query.edits[-1][0]
    assert button_data(query.edits[-1][1]) == [f"tx:{transaction_id}:restore"]

    press(env, f"tx:{transaction_id}:restore")
    assert env.db.get_transaction(transaction_id)["category"] == "Ingresos clientes"

    # El historial de cambios queda en audit_log y se puede deshacer desde el panel.
    env.db.undo_last_transaction_change(transaction_id)
    assert env.db.get_transaction(transaction_id)["category"] == "Trabajos extra"


def test_undo_command_removes_last_own_movement_and_can_restore(env):
    send_text(env, "gasto 5 pan", message_id=1)
    send_text(env, "gasto 7 leche", message_id=2)
    message = FakeMessage("/deshacer", message_id=3)
    update = SimpleNamespace(effective_user=env.user, effective_message=message)
    asyncio.run(bot.undo_command(update, env.context))
    [(text, markup)] = message.replies
    assert "7,00 €" in text and "leche" in text
    assert [row["note"] for row in env.db.list_transactions()] == ["pan"]
    press(env, button_data(markup)[0])
    assert [row["note"] for row in env.db.list_transactions()] == ["pan", "leche"]


def test_other_user_cannot_press_buttons(env):
    message = send_text(env, "gasto 5 pan")
    [(_, markup)] = message.replies
    query = FakeQuery(button_data(markup)[-1])
    update = SimpleNamespace(effective_user=SimpleNamespace(id=99), callback_query=query)
    asyncio.run(bot.transaction_button(update, env.context))
    assert query.edits == []
    assert len(env.db.list_transactions()) == 1


def _receipt(env, receipt_id_message=10, caption=None):
    return env.db.add_receipt(
        local_path=str(env.settings.data_dir / "ticket.jpg"), drive_file_id=None, drive_url=None,
        telegram_message_id=receipt_id_message, caption=caption, created_at="2026-10-03T19:30:00+02:00",
        telegram_user_id=1, telegram_username="ariel", telegram_full_name="Ariel",
    )


def test_reply_with_total_registers_the_ticket_on_its_own_date(env):
    receipt_id = _receipt(env)
    prompt = SimpleNamespace(text=f"📎 Ticket #{receipt_id} guardado (foto).\nResponde...", from_user=SimpleNamespace(is_bot=True))
    message = send_text(env, "21,40 Mercadona", message_id=11, reply_to=prompt)
    [(text, markup)] = message.replies
    assert f"Ticket #{receipt_id} registrado por su total" in text
    assert "Gasto · 21,40 € · Hogar y Alimentación · Mercadona" in text
    [row] = env.db.list_transactions()
    assert row["receipt_id"] == receipt_id
    assert row["review_status"] == SUMMARY_REVIEW_STATUS
    assert row["created_at"].startswith("2026-10-03")  # fecha del ticket, no de la respuesta
    assert env.db.get_receipt(receipt_id)["status"] == "processed"
    assert env.db.list_pending_files() == []

    # Un segundo total para el mismo ticket no duplica.
    again = send_text(env, "21,40 Mercadona", message_id=12, reply_to=prompt)
    assert "ya está registrado" in again.replies[0][0]
    assert len(env.db.list_transactions()) == 1

    # Deshacer devuelve el ticket a la bandeja; restaurar lo saca otra vez.
    press(env, f"tx:{row['id']}:undo")
    assert env.db.get_receipt(receipt_id)["status"] == "pending"
    press(env, f"tx:{row['id']}:restore")
    assert env.db.get_receipt(receipt_id)["status"] == "processed"


def test_hash_prefix_targets_a_specific_ticket_and_lines_replace_the_total(env):
    receipt_id = _receipt(env)
    message = send_text(env, f"#{receipt_id} 15 farmacia", message_id=11)
    assert "Salud & Cuidado" in message.replies[0][0]
    [summary] = env.db.list_transactions()
    assert summary["amount_cents"] == 1500

    created = env.db.register_receipt_entries(receipt_id, [
        {"kind": "expense", "amount_cents": 1000, "category": "Salud & Cuidado", "note": "Ibuprofeno"},
        {"kind": "expense", "amount_cents": 500, "category": "Hogar y Alimentación", "note": "Agua"},
    ])
    rows = env.db.list_transactions()
    assert [row["id"] for row in rows] == created
    assert env.db.get_transaction(summary["id"]) is None


def test_unknown_or_missing_total_gives_a_clear_answer(env):
    message = send_text(env, "#999 21,40", message_id=11)
    assert "No encuentro el ticket #999" in message.replies[0][0]
    receipt_id = _receipt(env)
    prompt = SimpleNamespace(text=f"📎 Ticket #{receipt_id} guardado (foto).")
    message = send_text(env, "mercadona", message_id=12, reply_to=prompt)
    assert "No veo el total" in message.replies[0][0]
    assert env.db.list_transactions() == []


def test_photo_with_amount_in_caption_is_registered_without_asking(env, monkeypatch):
    async def fake_download(context, file_id, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"jpg")

    monkeypatch.setattr(bot, "_download_file", fake_download)
    photo = [SimpleNamespace(file_id="f", file_unique_id="u1")]
    message = FakeMessage(None, message_id=20, caption="Lidl 18,20", photo=photo)
    update = SimpleNamespace(effective_user=env.user, effective_message=message)
    asyncio.run(bot.record_receipt(update, SimpleNamespace(application=env.context.application, bot=None)))
    [(text, _)] = message.replies
    assert "registrado por su total" in text and "18,20 €" in text
    [row] = env.db.list_transactions()
    assert row["store"] == "Lidl" and row["review_status"] == SUMMARY_REVIEW_STATUS

    plain = FakeMessage(None, message_id=21, photo=photo)
    update = SimpleNamespace(effective_user=env.user, effective_message=plain)
    asyncio.run(bot.record_receipt(update, SimpleNamespace(application=env.context.application, bot=None)))
    [(text, markup)] = plain.replies
    assert "Responde a este mensaje con el total" in text
    assert markup.selective is True


def test_weekly_summary_goes_out_once_on_sunday_evening_or_monday(env):
    sent = []

    async def send_message(**message):
        sent.append(message)

    app = SimpleNamespace(bot_data=env.context.application.bot_data, bot=SimpleNamespace(send_message=send_message))
    madrid = ZoneInfo("Europe/Madrid")
    assert weekly_period(datetime(2026, 10, 11, 18, 59, tzinfo=madrid)) is None  # domingo antes de las 19
    assert weekly_period(datetime(2026, 10, 11, 19, 0, tzinfo=madrid)) == "2026-W41"
    assert weekly_period(datetime(2026, 10, 12, 9, 0, tzinfo=madrid)) == "2026-W41"  # lunes: misma semana
    assert weekly_period(datetime(2026, 10, 13, 9, 0, tzinfo=madrid)) is None

    asyncio.run(send_weekly_summary(app, datetime(2026, 10, 11, 19, 0, tzinfo=madrid)))
    asyncio.run(send_weekly_summary(app, datetime(2026, 10, 12, 9, 0, tzinfo=madrid)))
    assert len(sent) == 1
    assert sent[0]["chat_id"] == 1
    assert "Resumen semanal" in sent[0]["text"]
    assert "Nada pendiente de revisar" in sent[0]["text"]

    app.bot_data["settings"] = replace(env.settings, weekly_summary_enabled=False)
    asyncio.run(send_weekly_summary(app, datetime(2026, 10, 18, 19, 0, tzinfo=madrid)))
    assert len(sent) == 1
