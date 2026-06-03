from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finance_bot.config import Settings
from finance_bot.db import FinanceDatabase
from finance_bot.formatting import parse_created_at
from finance_bot.parser import VALID_CATEGORIES, amount_to_cents
from finance_bot.report import generate_report, render_report_html


KIND_LABELS = {
    "expense": "expense",
    "egreso": "expense",
    "income": "income",
    "ingreso": "income",
}

PROJECTION_STATUSES = {"pending", "completed", "skipped"}


def _parse_kind(value: object) -> str:
    raw = str(value or "").strip().lower()
    kind = KIND_LABELS.get(raw)
    if not kind:
        raise ValueError("Tipo no valido. Usa Ingreso o Egreso.")
    return kind


def _parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "si", "sÃ­", "sí"}


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


class DashboardHandler(BaseHTTPRequestHandler):
    settings: Settings

    def log_message(self, format: str, *args: object) -> None:
        return

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
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
        path = urlparse(self.path).path
        if path in {"/", "/finanzas.html", "/reports/finanzas.html"}:
            self._send_html(render_report_html(self.settings, editable=True))
            return
        if path == "/favicon.ico":
            self._send(204, b"", "text/plain")
            return
        self._send_json(404, {"ok": False, "error": "Ruta no encontrada."})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        match = re.fullmatch(r"/api/transactions/(\d+)", path)
        projection_match = re.fullmatch(r"/api/projections/(\d+)/(\d{4}-\d{2})", path)
        projection_create = path == "/api/projections"
        if not match and not projection_match and not projection_create:
            self._send_json(404, {"ok": False, "error": "Ruta no encontrada."})
            return

        try:
            payload = self._read_json()
            if match:
                transaction_id = int(match.group(1))
                self._update_transaction(transaction_id, payload)
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

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        projection_match = re.fullmatch(r"/api/projections/(\d+)/(\d{4}-\d{2})", path)
        if not projection_match:
            self._send_json(404, {"ok": False, "error": "Ruta no encontrada."})
            return
        try:
            template_id = int(projection_match.group(1))
            month = _parse_month(projection_match.group(2))
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

        db.update_transaction(
            transaction_id,
            created_at=_created_at_for_date(existing["created_at"], payload.get("date")),
            kind=_parse_kind(payload.get("kind") or payload.get("type")),
            amount_cents=_parse_amount(payload.get("amount")),
            category=category,
            note=_text(payload.get("description") or payload.get("note"), required=True, field="descripcion"),
            store=_text(payload.get("store")),
            is_fixed=_parse_bool(payload.get("isFixed")),
        )
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
        start_month = template["start_month"] or month
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
        generate_report(self.settings)

    def _create_projection(self, payload: dict[str, object]) -> None:
        db = FinanceDatabase(self.settings.sqlite_db_path, self.settings.timezone)
        month = _parse_month(payload.get("month"))
        kind = _parse_kind(payload.get("kind"))
        name = _text(payload.get("name"), required=True, field="concepto")
        category = _text(payload.get("category"), required=True, field="categoria")
        if category not in VALID_CATEGORIES:
            raise ValueError("Categoria no valida.")
        amount_cents = _parse_amount(payload.get("amount"))
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
            start_month=month,
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
    settings.ensure_dirs()
    DashboardHandler.settings = settings
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
