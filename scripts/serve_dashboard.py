from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finance_bot.config import Settings
from finance_bot.db import FinanceDatabase
from finance_bot.formatting import parse_created_at
from finance_bot.parser import VALID_CATEGORIES, amount_to_cents
from finance_bot.report import generate_report, render_report_html, report_data
from finance_bot.storage import DatabaseUnavailableError, inspect_database, read_backup_status


KIND_LABELS = {
    "expense": "expense",
    "egreso": "expense",
    "income": "income",
    "ingreso": "income",
}

PROJECTION_STATUSES = {"pending", "completed", "skipped"}


def runtime_status_payload(settings: Settings) -> dict[str, object]:
    runtime_path = settings.data_dir / "runtime_status.json"
    runtime: dict[str, object] = {}
    supervisor_running = False
    try:
        loaded = json.loads(runtime_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            runtime = loaded
            updated_at = datetime.fromisoformat(str(runtime.get("updated_at")))
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            supervisor_running = (
                datetime.now(timezone.utc) - updated_at.astimezone(timezone.utc)
            ).total_seconds() <= 10
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        runtime = {}

    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone)
    with db._connect() as connection:
        pending_count = connection.execute(
            "SELECT COUNT(*) FROM receipts WHERE status IN ('nuevo','pending','voice_pending','dudoso','missing')"
        ).fetchone()[0]
        last_transaction = connection.execute("SELECT created_at FROM transactions ORDER BY created_at DESC, id DESC LIMIT 1").fetchone()
        last_receipt = connection.execute("SELECT created_at FROM receipts ORDER BY created_at DESC, id DESC LIMIT 1").fetchone()
    return {
        "ok": True,
        "supervisor": {
            "running": supervisor_running,
            "pid": runtime.get("supervisor_pid") if supervisor_running else None,
            "startedAt": runtime.get("started_at") if supervisor_running else None,
        },
        "bot": {
            "running": bool(runtime.get("bot_running")) if supervisor_running else None,
            "pid": runtime.get("bot_pid") if supervisor_running else None,
            "exitCode": runtime.get("bot_exit_code") if supervisor_running else None,
        },
        "dashboard": {
            "running": True,
            "pid": os.getpid(),
        },
        "pendingCount": int(pending_count),
        "lastTransactionAt": last_transaction[0] if last_transaction else None,
        "lastReceiptAt": last_receipt[0] if last_receipt else None,
        "lastBackupAt": read_backup_status(settings.data_dir).get("verifiedAt"),
    }


def _parse_kind(value: object) -> str:
    raw = str(value or "").strip().lower()
    kind = KIND_LABELS.get(raw)
    if not kind:
        raise ValueError("Tipo no valido. Usa Ingreso o Egreso.")
    return kind


def _parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "si", "sí"}


def _parse_amount(value: object) -> int:
    raw = str(value or "").strip().replace("€", "").replace("EUR", "")
    if not raw:
        raise ValueError("Falta la cantidad.")
    return amount_to_cents(raw)


def _parse_projection_status(value: object) -> str:
    status = str(value or "").strip().lower()
    if status not in PROJECTION_STATUSES:
        raise ValueError("Estado no valido.")
    return status


def _parse_month(value: object) -> str:
    month = str(value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}", month):
        raise ValueError("Mes no valido.")
    date.fromisoformat(month + "-01")
    return month


def _month_distance(start_month: str, end_month: str) -> int:
    start = date.fromisoformat(start_month + "-01")
    end = date.fromisoformat(end_month + "-01")
    return (end.year - start.year) * 12 + (end.month - start.month)


def _parse_optional_int(value: object) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    number = int(text)
    if number < 1:
        raise ValueError("Las cuotas deben ser mayores que cero.")
    return number


def _projection_installments(payload: dict[str, object], *, month: str, start_month: str) -> tuple[int | None, int | None]:
    duration = str(payload.get("duration") or "monthly").strip().lower()
    if duration == "monthly":
        return None, None
    if duration == "once":
        return 1, 1
    if duration != "installments":
        raise ValueError("Duracion no valida.")

    current_at_selected_month = _parse_optional_int(payload.get("installmentCurrent")) or 1
    total = _parse_optional_int(payload.get("installmentTotal"))
    if total is None:
        raise ValueError("Falta el total de cuotas.")
    if current_at_selected_month > total:
        raise ValueError("La cuota actual no puede superar el total.")

    base_current = current_at_selected_month - _month_distance(start_month, month)
    if base_current < 1:
        raise ValueError("La cuota actual no cuadra con el mes de inicio.")
    return base_current, total


def _created_at_for_date(existing_created_at: str, raw_date: object) -> str:
    raw = str(raw_date or "").strip()
    if not raw:
        raise ValueError("Falta la fecha.")
    selected = date.fromisoformat(raw)
    current = parse_created_at(existing_created_at)
    updated = current.replace(year=selected.year, month=selected.month, day=selected.day)
    return updated.isoformat(timespec="seconds")


def _text(value: object, *, required: bool = False, field: str = "campo") -> str:
    text = str(value or "").strip()
    if required and not text:
        raise ValueError(f"Falta {field}.")
    return text


def set_projection_status(settings: Settings, template_id: int, month: str, status: str) -> None:
    """Cambia solo el estado de un mes proyectado, conservando importe y nota."""
    status = _parse_projection_status(status)
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone)
    template = db.get_projection_template(template_id)
    if template is None:
        raise KeyError(f"No existe la proyeccion {template_id}.")
    occurrence = db.get_projection_occurrence(template_id, month)
    amount_cents = (
        int(occurrence["amount_cents"])
        if occurrence is not None
        else int(template["default_amount_cents"])
    )
    note = str(occurrence["note"]) if occurrence is not None else ""
    db.set_projection_occurrence(
        template_id=template_id,
        month=month,
        amount_cents=amount_cents,
        status=status,
        note=note,
    )
    if status == "completed":
        db.register_projection_payment(template_id, month)
    else:
        # Volver a pendiente u omitir deshace el cargo automatico de ese mes.
        db.drop_auto_payments(template_id, month)
    db.log_audit("status", "projection", template_id, f"Mes {month}: {status}")
    generate_report(settings)


class DashboardHandler(BaseHTTPRequestHandler):
    settings: Settings
    # Hosts que este servidor acepta como destino legitimo de una escritura.
    # No basta con comparar el header Origin contra el propio Host: con DNS
    # rebinding, una pagina servida desde un dominio ajeno puede hacer que el
    # navegador resuelva ese dominio a 127.0.0.1 y enviar Origin/Host iguales
    # entre si sin que ninguno sea realmente este servidor. Se compara contra
    # una lista fija conocida en el arranque en su lugar.
    allowed_hosts: set[str] = set()

    def log_message(self, format: str, *args: object) -> None:
        return

    def _origin_allowed(self) -> bool:
        host = self.headers.get("Host", "")
        if not self._host_allowed():
            return False
        origin = self.headers.get("Origin")
        return not origin or origin == "http://" + host

    def _host_allowed(self) -> bool:
        allowed = self.allowed_hosts
        if not allowed:
            address, port = self.server.server_address[:2]
            allowed = {f"{address}:{port}"}
            if address == "127.0.0.1":
                allowed.add(f"localhost:{port}")
        return self.headers.get("Host", "") in allowed

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status: int, payload: dict[str, object]) -> None:
        self._send(
            status,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _send_html(self, html: str) -> None:
        self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")

    def do_GET(self) -> None:
        if not self._host_allowed():
            self._send_json(403, {"ok": False, "error": "Destino de consulta no permitido."})
            return
        try:
            self._get()
        except DatabaseUnavailableError as exc:
            if urlparse(self.path).path.startswith("/api/"):
                self._send_json(503, {"ok": False, "error": str(exc)})
            else:
                import html
                self._send(503, ("<!doctype html><html lang='es'><meta charset='utf-8'><title>Revisar base de finanzas</title>"
                    "<body style='font:18px system-ui;max-width:720px;margin:80px auto;padding:24px'>"
                    "<h1>No podemos mostrar tus finanzas</h1><p>La base de datos no está disponible. "
                    "Tus cifras no se han sustituido por ceros.</p><p>" + html.escape(str(exc)) + "</p></body></html>").encode(), "text/html; charset=utf-8")

    def _get(self) -> None:
        path = urlparse(self.path).path
        if path in {"/", "/finanzas.html", "/reports/finanzas.html"}:
            self._send_html(render_report_html(self.settings, editable=True))
            return
        if path == "/api/status":
            self._send_json(200, runtime_status_payload(self.settings))
            return
        if path == "/api/data":
            self._send_json(200, report_data(self.settings, editable=True))
            return
        attachment = re.fullmatch(r"/api/attachments/(transactions|receipts)/(\d+)", path)
        if attachment:
            self._attachment(attachment.group(1), int(attachment.group(2)))
            return
        if path == "/favicon.ico":
            self._send(204, b"", "text/plain")
            return
        self._send_json(404, {"ok": False, "error": "Ruta no encontrada."})

    def _attachment(self, kind: str, item_id: int) -> None:
        db = FinanceDatabase(self.settings.sqlite_db_path, self.settings.timezone)
        row = db.get_transaction(item_id) if kind == "transactions" else db.get_receipt(item_id)
        value = row["receipt_local_path" if kind == "transactions" else "local_path"] if row else None
        path = Path(value) if value else None
        allowed = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".pdf", ".ogg", ".mp3", ".wav", ".m4a", ".opus"}
        if not path or path.suffix.lower() not in allowed:
            self._send_json(404, {"ok": False, "error": "Adjunto no disponible o formato no compatible."})
            return
        try:
            body = path.read_bytes()
        except OSError:
            self._send_json(404, {"ok": False, "error": "No se encuentra el archivo. Comprueba la carpeta sincronizada."})
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix.lower() in {".ogg", ".opus"}:
            content_type = "audio/ogg"
        self._send(200, body, content_type)

    def do_POST(self) -> None:
        if not self._origin_allowed():
            self._send_json(403, {"ok": False, "error": "Origen de edición no permitido."})
            return
        path = urlparse(self.path).path
        match = re.fullmatch(r"/api/transactions/(\d+)", path)
        undo_match = re.fullmatch(r"/api/transactions/(\d+)/undo", path)
        restore_match = re.fullmatch(r"/api/transactions/(\d+)/restore", path)
        payment_match = re.fullmatch(r"/api/transactions/(\d+)/payment", path)
        create_transaction = path == "/api/transactions"
        create_account = path == "/api/accounts"
        create_transfer = path == "/api/transfers"
        create_budget = path == "/api/budgets"
        create_goal = path == "/api/savings-goals"
        savings_settings = path == "/api/savings-settings"
        savings_wallet = path == "/api/savings-wallets"
        receipt_review = re.fullmatch(r"/api/receipts/(\d+)/review", path)
        status_match = re.fullmatch(r"/api/projections/(\d+)/(\d{4}-\d{2})/status", path)
        projection_end_match = re.fullmatch(r"/api/projections/(\d+)/(\d{4}-\d{2})/from", path)
        projection_match = re.fullmatch(r"/api/projections/(\d+)/(\d{4}-\d{2})", path)
        projection_create = path == "/api/projections"
        if not match and not undo_match and not restore_match and not payment_match and not create_transaction and not create_account and not create_transfer and not create_budget and not create_goal and not savings_settings and not savings_wallet and not receipt_review and not status_match and not projection_end_match and not projection_match and not projection_create:
            self._send_json(404, {"ok": False, "error": "Ruta no encontrada."})
            return

        try:
            payload = self._read_json()
            if create_transaction:
                self._create_transaction(payload)
            elif create_account:
                db = FinanceDatabase(self.settings.sqlite_db_path, self.settings.timezone)
                account_id = db.create_account(_text(payload.get("name"), required=True, field="nombre"), _text(payload.get("type") or "bank"), _parse_amount(payload.get("openingBalance") or "0"))
                db.log_audit("create", "account", account_id, "Creada desde el panel")
                generate_report(self.settings)
            elif create_transfer:
                db = FinanceDatabase(self.settings.sqlite_db_path, self.settings.timezone)
                transfer_id = db.add_transfer(from_account_id=int(payload.get("fromAccountId")), to_account_id=int(payload.get("toAccountId")), amount_cents=_parse_amount(payload.get("amount")), note=_text(payload.get("note")))
                db.log_audit("create", "transfer", transfer_id, "Transferencia interna")
                generate_report(self.settings)
            elif create_budget:
                db = FinanceDatabase(self.settings.sqlite_db_path, self.settings.timezone)
                db.upsert_budget(_parse_month(payload.get("month")), _text(payload.get("category"), required=True, field="categoria"), _parse_amount(payload.get("amount")))
                generate_report(self.settings)
            elif create_goal:
                db = FinanceDatabase(self.settings.sqlite_db_path, self.settings.timezone)
                goal_id = db.create_savings_goal(_text(payload.get("name"), required=True, field="nombre"), _parse_amount(payload.get("target")), _text(payload.get("targetDate")) or None, int(payload["walletId"]) if str(payload.get("walletId") or "") else None)
                db.log_audit("create", "savings_goal", goal_id, "Creado desde el panel")
                generate_report(self.settings)
            elif savings_settings:
                db = FinanceDatabase(self.settings.sqlite_db_path, self.settings.timezone)
                db.update_savings_settings(
                    current_balance_cents=_parse_amount(payload.get("currentBalance") or "0"),
                    current_balance_date=_text(payload.get("currentBalanceDate")),
                    include_current_balance=_parse_bool(payload.get("includeCurrentBalance")),
                    emergency_monthly_cents=_parse_amount(payload.get("emergencyMonthly") or "0"),
                )
                generate_report(self.settings)
            elif savings_wallet:
                db = FinanceDatabase(self.settings.sqlite_db_path, self.settings.timezone)
                wallet_id = int(payload.get("id")) if str(payload.get("id") or "") else None
                if payload.get("archive"):
                    if wallet_id is None:
                        raise ValueError("Falta la cartera")
                    row = next((item for item in db.list_savings_wallets(active_only=False) if item["id"] == wallet_id), None)
                    if row is None:
                        raise KeyError(f"No existe la cartera {wallet_id}")
                    db.update_savings_wallet(wallet_id, name=row["name"], balance_cents=row["balance_cents"], include_in_projection=bool(row["include_in_projection"]), active=False)
                elif wallet_id is None:
                    db.create_savings_wallet(_text(payload.get("name"), required=True, field="nombre"), _parse_amount(payload.get("balance") or "0"), _parse_bool(payload.get("includeInProjection")))
                else:
                    db.update_savings_wallet(wallet_id, name=_text(payload.get("name"), required=True, field="nombre"), balance_cents=_parse_amount(payload.get("balance") or "0"), include_in_projection=_parse_bool(payload.get("includeInProjection")), active=True)
                generate_report(self.settings)
            elif receipt_review:
                self._review_receipt(int(receipt_review.group(1)), payload)
            elif undo_match:
                db = FinanceDatabase(self.settings.sqlite_db_path, self.settings.timezone)
                db.undo_last_transaction_change(int(undo_match.group(1)))
                generate_report(self.settings)
            elif restore_match:
                db = FinanceDatabase(self.settings.sqlite_db_path, self.settings.timezone)
                db.restore_deleted_transaction(int(restore_match.group(1)))
                generate_report(self.settings)
            elif payment_match:
                db = FinanceDatabase(self.settings.sqlite_db_path, self.settings.timezone)
                db.set_transaction_payment(int(payment_match.group(1)), _parse_amount(payload.get("paidAmount")), _text(payload.get("dueDate")) or None)
                generate_report(self.settings)
            elif match:
                transaction_id = int(match.group(1))
                self._update_transaction(transaction_id, payload)
            elif status_match:
                template_id = int(status_match.group(1))
                month = _parse_month(status_match.group(2))
                set_projection_status(self.settings, template_id, month, str(payload.get("status") or ""))
            elif projection_end_match:
                template_id = int(projection_end_match.group(1))
                month = _parse_month(projection_end_match.group(2))
                db = FinanceDatabase(self.settings.sqlite_db_path, self.settings.timezone)
                db.end_projection_from(template_id, month)
                generate_report(self.settings)
            elif projection_match:
                assert projection_match is not None
                template_id = int(projection_match.group(1))
                month = _parse_month(projection_match.group(2))
                self._update_projection(template_id, month, payload)
            elif projection_create:
                self._create_projection(payload)
        except KeyError as exc:
            self._send_json(404, {"ok": False, "error": str(exc)})
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": f"No se pudo guardar: {exc}"})
        else:
            self._send_json(200, {"ok": True})

    def _create_transaction(self, payload: dict[str, object]) -> None:
        db = FinanceDatabase(self.settings.sqlite_db_path, self.settings.timezone)
        kind = _parse_kind(payload.get("kind") or payload.get("type"))
        category = _text(payload.get("category"), required=True, field="categoria")
        if category not in VALID_CATEGORIES:
            raise ValueError("Categoria no valida.")
        amount_cents = _parse_amount(payload.get("amount"))
        if amount_cents <= 0:
            raise ValueError("El importe debe ser mayor que cero.")
        note = _text(payload.get("description") or payload.get("note"), required=True, field="descripción")
        raw_date = _text(payload.get("date"), required=True, field="fecha")
        selected = date.fromisoformat(raw_date)
        created_at = datetime.combine(selected, datetime.min.time()).replace(tzinfo=db.timezone).isoformat(timespec="seconds")
        transaction_id = db.add_manual_transaction(
            kind=kind, amount_cents=amount_cents, category=category, note=note,
            store=_text(payload.get("store")), is_fixed=_parse_bool(payload.get("isFixed")),
            source_text="Panel local", created_at=created_at,
            account_id=int(payload["accountId"]) if str(payload.get("accountId") or "") else None,
        )
        db.log_audit("create", "transaction", transaction_id, "Creado desde el panel")
        generate_report(self.settings)

    def _review_receipt(self, receipt_id: int, payload: dict[str, object]) -> None:
        entries = payload.get("entries")
        if not isinstance(entries, list):
            raise ValueError("Faltan las líneas del ticket")
        db = FinanceDatabase(self.settings.sqlite_db_path, self.settings.timezone)
        normalized = []
        for item in entries:
            if not isinstance(item, dict):
                raise ValueError("Formato de línea no válido")
            item_date = str(item.get("date") or "").strip()
            created_at = None
            if item_date:
                selected = date.fromisoformat(item_date)
                created_at = datetime.combine(selected, datetime.min.time()).replace(tzinfo=db.timezone).isoformat(timespec="seconds")
            normalized.append({
                "kind": item.get("kind") or "expense",
                "amount_cents": _parse_amount(item.get("amount")),
                "category": item.get("category"),
                "note": item.get("description") or item.get("note"),
                "store": item.get("store"),
                "is_fixed": _parse_bool(item.get("isFixed")),
                "created_at": created_at,
            })
        if str(payload.get("expectedTotal") or "").strip():
            expected = _parse_amount(payload["expectedTotal"])
            if expected <= 0 or sum(row["amount_cents"] for row in normalized) != expected:
                raise ValueError("La suma de las líneas no coincide con el total del justificante. Revisa los importes.")
        transaction_ids = db.register_receipt_entries(receipt_id, normalized, allow_existing=_parse_bool(payload.get("allowExisting")))
        db.log_audit("review", "receipt", receipt_id, json.dumps({"transactions": transaction_ids}, ensure_ascii=False))
        generate_report(self.settings)

    def do_DELETE(self) -> None:
        if not self._origin_allowed():
            self._send_json(403, {"ok": False, "error": "Origen de edición no permitido."})
            return
        path = urlparse(self.path).path
        projection_end_match = re.fullmatch(r"/api/projections/(\d+)/(\d{4}-\d{2})/from", path)
        projection_match = re.fullmatch(r"/api/projections/(\d+)/(\d{4}-\d{2})", path)
        transaction_match = re.fullmatch(r"/api/transactions/(\d+)", path)
        if not projection_match and not projection_end_match and not transaction_match:
            self._send_json(404, {"ok": False, "error": "Ruta no encontrada."})
            return
        try:
            if transaction_match:
                db = FinanceDatabase(self.settings.sqlite_db_path, self.settings.timezone)
                db.delete_transaction(int(transaction_match.group(1)))
                generate_report(self.settings)
                self._send_json(200, {"ok": True})
                return
            selected = projection_end_match or projection_match
            assert selected is not None
            template_id = int(selected.group(1))
            month = _parse_month(selected.group(2))
            if projection_end_match:
                db = FinanceDatabase(self.settings.sqlite_db_path, self.settings.timezone)
                db.end_projection_from(template_id, month)
                generate_report(self.settings)
            else:
                self._delete_projection_month(template_id, month)
        except KeyError as exc:
            self._send_json(404, {"ok": False, "error": str(exc)})
        except ValueError as exc:
            self._send_json(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": f"No se pudo borrar: {exc}"})
        else:
            self._send_json(200, {"ok": True})

    def _read_json(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            raise ValueError("Faltan datos para guardar.")
        if length > 64_000:
            raise ValueError("La edicion es demasiado grande.")
        data = self.rfile.read(length).decode("utf-8")
        payload = json.loads(data)
        if not isinstance(payload, dict):
            raise ValueError("Formato no valido.")
        return payload

    def _update_transaction(self, transaction_id: int, payload: dict[str, object]) -> None:
        db = FinanceDatabase(self.settings.sqlite_db_path, self.settings.timezone)
        existing = db.get_transaction(transaction_id)
        if existing is None:
            raise KeyError(f"No existe el movimiento {transaction_id}.")

        category = _text(payload.get("category"), required=True, field="categoria")
        if category not in VALID_CATEGORIES:
            raise ValueError("Categoria no valida.")

        values = {
            "created_at": _created_at_for_date(existing["created_at"], payload.get("date")),
            "kind": _parse_kind(payload.get("kind") or payload.get("type")),
            "amount_cents": _parse_amount(payload.get("amount")),
            "category": category,
            "note": _text(payload.get("description") or payload.get("note"), required=True, field="descripción"),
            "store": _text(payload.get("store")),
            "is_fixed": int(_parse_bool(payload.get("isFixed"))),
            "account_id": int(payload["accountId"]) if str(payload.get("accountId") or "") else None,
        }
        if values["amount_cents"] <= 0:
            raise ValueError("El importe debe ser mayor que cero; usa el tipo para indicar ingreso o gasto.")
        if existing["paid_amount_cents"] == existing["amount_cents"]:
            values["paid_amount_cents"] = values["amount_cents"]
        if "userId" in payload:
            user_id = int(payload["userId"]) if str(payload["userId"] or "") else None
            users = {r["telegram_user_id"]: r for r in db.list_transactions() if r["telegram_user_id"] is not None}
            if user_id is not None and user_id not in users and user_id not in self.settings.telegram_user_aliases:
                raise ValueError("Usuario no reconocido.")
            values["telegram_user_id"] = user_id
            values["telegram_username"] = users[user_id]["telegram_username"] if user_id in users else None
            values["telegram_full_name"] = self.settings.telegram_user_aliases.get(user_id) or (users[user_id]["telegram_full_name"] if user_id in users else None)
        if "receiptId" in payload:
            receipt_id = int(payload["receiptId"]) if str(payload["receiptId"] or "") else None
            receipt = db.get_receipt(receipt_id) if receipt_id is not None else None
            if receipt_id is not None and receipt is None:
                raise ValueError("Archivo no reconocido.")
            # Legacy path-only attachments stay intact unless explicitly removed.
            if receipt_id != existing["receipt_id"] or payload.get("removeAttachment"):
                values.update(receipt_id=receipt_id, receipt_local_path=receipt["local_path"] if receipt else None)
        if _parse_bool(payload.get("reviewed")):
            values.update(inference_notes="[]", review_status="reviewed")
        old_link = existing["projection_template_id"]
        new_link = old_link
        if "projectionTemplateId" in payload:
            new_link = int(payload["projectionTemplateId"]) if str(payload["projectionTemplateId"] or "") else None
        new_month = str(values["created_at"])[:7]
        old_month = str(existing["created_at"])[:7]
        template = db.get_projection_template(new_link) if new_link is not None else None
        if new_link is not None and (not template or template["kind"] != values["kind"] or not db._projection_applies_to_month(template, new_month)):
            raise ValueError("La proyección no corresponde al tipo o mes. Selecciona otra o desvincúlala.")
        values["projection_template_id"] = new_link
        with db.atomic(), db._connect() as connection:
            if dict(db.get_transaction(transaction_id)) != dict(existing):
                raise ValueError("Este movimiento cambió mientras lo editabas. Actualiza los datos y vuelve a intentarlo.")
            keys = {(link, month) for link, month in ((old_link, old_month), (new_link, new_month)) if link}
            projection_snapshots = []
            for link, month in sorted(keys):
                occurrence = db.get_projection_occurrence(link, month)
                projection_snapshots.append({"templateId": link, "month": month, "before": dict(occurrence) if occurrence else None})
            connection.execute("UPDATE transactions SET " + ", ".join(key + " = ?" for key in values) + " WHERE id = ?", (*values.values(), transaction_id))
            if old_link and (old_link != new_link or old_month != new_month):
                remaining = connection.execute("SELECT COUNT(*) FROM transactions WHERE projection_template_id = ? AND substr(created_at,1,7) = ?", (old_link, old_month)).fetchone()[0]
                if not remaining:
                    connection.execute("UPDATE projection_occurrences SET status = 'pending', note = '', updated_at = ? WHERE template_id = ? AND month = ?", (db._now(), old_link, old_month))
            if template and (old_link != new_link or old_month != new_month):
                connection.execute("""INSERT INTO projection_occurrences(template_id, month, amount_cents, status, note, updated_at)
                    VALUES (?, ?, ?, 'completed', ?, ?) ON CONFLICT(template_id,month)
                    DO UPDATE SET status = 'completed', updated_at = excluded.updated_at""",
                    (new_link, new_month, template["default_amount_cents"], f"Vinculado manualmente al movimiento #{transaction_id}", db._now()))
            for snapshot in projection_snapshots:
                occurrence = db.get_projection_occurrence(snapshot["templateId"], snapshot["month"])
                snapshot["after"] = dict(occurrence) if occurrence else None
            if new_link:
                db.drop_auto_payments(new_link, new_month, keep=transaction_id)
            db.log_audit("update", "transaction", transaction_id,
                         json.dumps({"before": dict(existing), "after": values, "projections": projection_snapshots}, ensure_ascii=False, default=str))
            if values["category"] != existing["category"]:
                # La proxima vez que llegue este mismo concepto por Telegram se usa esta categoria.
                db.learn_category(values["note"], values["kind"], values["category"])
        generate_report(self.settings)

    def _update_projection(
        self, template_id: int, month: str, payload: dict[str, object]
    ) -> None:
        db = FinanceDatabase(self.settings.sqlite_db_path, self.settings.timezone)
        template = db.get_projection_template(template_id)
        if template is None:
            raise KeyError(f"No existe la proyeccion {template_id}.")

        name = _text(payload.get("name"), required=True, field="concepto")
        category = _text(payload.get("category"), required=True, field="categoria")
        if category not in VALID_CATEGORIES:
            raise ValueError("Categoria no valida.")

        amount_cents = _parse_amount(payload.get("amount"))
        if amount_cents <= 0:
            raise ValueError("El importe previsto debe ser mayor que cero.")
        start_month = template["start_month"] or month
        raw_start = str(payload.get("startMonth") or start_month)
        start_month = _parse_month(raw_start)
        raw_end = str(payload.get("endMonth") or "").strip()
        end_month = _parse_month(raw_end) if raw_end else ""
        if end_month and end_month < start_month:
            raise ValueError("El mes final no puede ser anterior al inicio")
        if payload.get("duration") in {"once", "installments"} and not template["installment_total"]:
            start_month = month
        installment_current, installment_total = _projection_installments(
            payload, month=month, start_month=start_month
        )
        db.update_projection_template(
            template_id,
            name=name,
            kind=_parse_kind(payload.get("kind") or template["kind"]),
            category=category,
            start_month=start_month,
            end_month=end_month,
            default_amount_cents=amount_cents if _parse_bool(payload.get("updateDefault")) else None,
            installment_current=installment_current,
            installment_total=installment_total,
            clear_installments=installment_current is None and installment_total is None,
        )
        db.set_projection_occurrence(
            template_id=template_id,
            month=month,
            amount_cents=amount_cents,
            status=_parse_projection_status(payload.get("status")),
            note=_text(payload.get("note")),
        )
        self._apply_auto_register(db, template_id, month, payload)
        self._apply_weekly_budget(db, template_id, payload)
        db.log_audit("update", "projection", template_id, f"Mes {month}")
        generate_report(self.settings)

    @staticmethod
    def _apply_weekly_budget(db: FinanceDatabase, template_id: int, payload: dict[str, object]) -> None:
        if "weeklyBudget" not in payload:
            return
        raw = str(payload.get("weeklyBudget") or "").strip()
        db.set_projection_weekly_budget(template_id, _parse_amount(raw) if raw else None)

    @staticmethod
    def _apply_auto_register(db: FinanceDatabase, template_id: int, month: str, payload: dict[str, object]) -> None:
        if "autoRegister" not in payload:
            return
        db.set_projection_auto_register(template_id, _parse_bool(payload.get("autoRegister")))
        occurrence = db.get_projection_occurrence(template_id, month)
        if occurrence is not None and occurrence["status"] == "completed":
            # Al activarlo sobre un mes ya pagado, se registra su movimiento.
            db.register_projection_payment(template_id, month)

    def _create_projection(self, payload: dict[str, object]) -> None:
        db = FinanceDatabase(self.settings.sqlite_db_path, self.settings.timezone)
        month = _parse_month(payload.get("month"))
        kind = _parse_kind(payload.get("kind"))
        name = _text(payload.get("name"), required=True, field="concepto")
        category = _text(payload.get("category"), required=True, field="categoria")
        if category not in VALID_CATEGORIES:
            raise ValueError("Categoria no valida.")
        amount_cents = _parse_amount(payload.get("amount"))
        if amount_cents <= 0:
            raise ValueError("El importe previsto debe ser mayor que cero.")
        start_month = _parse_month(payload.get("startMonth") or month)
        raw_end = str(payload.get("endMonth") or "").strip()
        end_month = _parse_month(raw_end) if raw_end else ""
        if end_month and end_month < start_month:
            raise ValueError("El mes final no puede ser anterior al inicio")
        installment_current, installment_total = _projection_installments(
            payload, month=month, start_month=month
        )
        sort_order = len(db.list_projection_templates(active_only=False)) + 1
        template_id = db.upsert_projection_template(
            kind=kind,
            name=name,
            default_amount_cents=amount_cents,
            category=category,
            group_name="Agregado manual",
            start_month=start_month,
            end_month=end_month,
            installment_current=installment_current,
            installment_total=installment_total,
            sort_order=sort_order,
        )
        db.set_projection_occurrence(
            template_id=template_id,
            month=month,
            amount_cents=amount_cents,
            status=_parse_projection_status(payload.get("status")),
            note=_text(payload.get("note")),
        )
        self._apply_auto_register(db, template_id, month, payload)
        generate_report(self.settings)

    def _delete_projection_month(self, template_id: int, month: str) -> None:
        db = FinanceDatabase(self.settings.sqlite_db_path, self.settings.timezone)
        template = db.get_projection_template(template_id)
        if template is None:
            raise KeyError(f"No existe la proyeccion {template_id}.")
        db.set_projection_occurrence(
            template_id=template_id,
            month=month,
            amount_cents=int(template["default_amount_cents"]),
            status="skipped",
            note="Borrado del mes proyectado",
        )
        generate_report(self.settings)


def main() -> None:
    parser = argparse.ArgumentParser(description="Panel local editable de finanzas")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    settings = Settings.from_env()
    inspect_database(settings.sqlite_db_path, check_integrity=True)
    settings.ensure_dirs()
    DashboardHandler.settings = settings
    DashboardHandler.allowed_hosts = {f"{args.host}:{args.port}"}
    if args.host == "127.0.0.1":
        DashboardHandler.allowed_hosts.add(f"localhost:{args.port}")
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    url = f"http://{args.host}:{args.port}"
    print(f"Panel editable: {url}")
    print("Pulsa Ctrl+C para cerrar.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
