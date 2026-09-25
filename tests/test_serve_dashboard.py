from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from finance_bot.config import Settings
from finance_bot.db import FinanceDatabase


def _load_serve_dashboard():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "serve_dashboard.py"
    spec = importlib.util.spec_from_file_location("serve_dashboard_module", module_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    return Settings(
        telegram_bot_token="test-token",
        allowed_telegram_user_ids=None,
        telegram_user_aliases={},
        data_dir=data_dir,
        sqlite_db_path=data_dir / "finances.db",
        export_csv_path=data_dir / "movimientos.csv",
        report_html_path=tmp_path / "reports" / "finanzas.html",
        receipts_sync_dir=data_dir / "receipts",
        voices_sync_dir=data_dir / "voices",
        timezone="Europe/Madrid",
        prefer_codex_media_review=True,
        voice_transcription_enabled=False,
        voice_transcription_model="base",
        voice_transcription_device="cpu",
        voice_transcription_compute_type="int8",
    )


def _template(db: FinanceDatabase) -> int:
    return db.upsert_projection_template(
        kind="expense",
        name="Alquiler",
        default_amount_cents=65500,
        category="Alquiler",
        start_month="2026-07",
    )


def test_transaction_link_edit_is_validated_before_writes_and_can_unlink(tmp_path):
    module = _load_serve_dashboard()
    settings = _settings(tmp_path)
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone, create=True)
    template = _template(db)
    transaction = db.add_manual_transaction(kind="expense", amount_cents=65500, category="Alquiler", note="Alquiler", created_at="2026-07-04T12:00:00+02:00")
    handler = object.__new__(module.DashboardHandler)
    handler.settings = settings
    payload = {"date": "2026-07-04", "amount": "655,00", "description": "Alquiler revisado", "category": "Alquiler", "kind": "expense", "isFixed": True, "projectionTemplateId": 99999}
    with pytest.raises(ValueError):
        handler._update_transaction(transaction, payload)
    assert db.get_transaction(transaction)["note"] == "Alquiler"
    payload.update(projectionTemplateId="", reviewed=True)
    handler._update_transaction(transaction, payload)
    assert db.get_transaction(transaction)["projection_template_id"] is None
    assert db.get_transaction(transaction)["review_status"] == "reviewed"
    assert db.get_projection_occurrence(template, "2026-07")["status"] == "pending"

    restored = db.undo_last_transaction_change(transaction)
    assert restored["note"] == "Alquiler"
    assert restored["projection_template_id"] == template
    assert restored["review_status"] == "registered"
    assert db.get_projection_occurrence(template, "2026-07")["status"] == "completed"


def test_attachment_http_routes_only_serve_registered_media(tmp_path):
    from http.server import ThreadingHTTPServer
    from threading import Thread
    from urllib.request import urlopen
    from urllib.error import HTTPError
    module = _load_serve_dashboard()
    settings = _settings(tmp_path)
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone, create=True)
    path = tmp_path / "ticket.pdf"
    path.write_bytes(b"%PDF-1.4 test")
    receipt = db.add_receipt(local_path=str(path), drive_file_id=None, drive_url=None, telegram_message_id=None, caption=None, status="processed")
    class Handler(module.DashboardHandler):
        pass
    Handler.settings = settings
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = "http://127.0.0.1:" + str(server.server_port)
    try:
        with urlopen(base + f"/api/attachments/receipts/{receipt}") as response:
            assert response.read() == b"%PDF-1.4 test"
            assert response.headers["Content-Type"] == "application/pdf"
            assert response.headers["X-Content-Type-Options"] == "nosniff"
        for url in ["/api/attachments/receipts/99999", "/api/attachments/../../.env"]:
            with pytest.raises(HTTPError) as error:
                urlopen(base + url)
            assert error.value.code == 404
        path.unlink()
        with pytest.raises(HTTPError) as error:
            urlopen(base + f"/api/attachments/receipts/{receipt}")
        assert error.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_read_routes_reject_untrusted_host_header(tmp_path) -> None:
    from http.client import HTTPConnection
    from http.server import ThreadingHTTPServer
    from threading import Thread

    module = _load_serve_dashboard()
    settings = _settings(tmp_path)
    FinanceDatabase(settings.sqlite_db_path, settings.timezone, create=True)

    class Handler(module.DashboardHandler):
        allowed_hosts = set()

    Handler.settings = settings
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port)
        connection.putrequest("GET", "/api/data", skip_host=True)
        connection.putheader("Host", "evil.example")
        connection.endheaders()
        response = connection.getresponse()
        assert response.status == 403
        response.read()
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def test_set_projection_status_marks_completed_with_default_amount(tmp_path) -> None:
    serve_dashboard = _load_serve_dashboard()
    settings = _settings(tmp_path)
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone, create=True)
    template_id = _template(db)

    serve_dashboard.set_projection_status(settings, template_id, "2026-07", "completed")

    occurrence = db.get_projection_occurrence(template_id, "2026-07")
    assert occurrence is not None
    assert occurrence["status"] == "completed"
    assert occurrence["amount_cents"] == 65500
    assert settings.report_html_path.exists()


def test_set_projection_status_preserves_month_amount_and_note(tmp_path) -> None:
    serve_dashboard = _load_serve_dashboard()
    settings = _settings(tmp_path)
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone, create=True)
    template_id = _template(db)
    db.set_projection_occurrence(
        template_id=template_id,
        month="2026-08",
        amount_cents=70000,
        status="pending",
        note="Subida de alquiler",
    )

    serve_dashboard.set_projection_status(settings, template_id, "2026-08", "completed")

    occurrence = db.get_projection_occurrence(template_id, "2026-08")
    assert occurrence is not None
    assert occurrence["status"] == "completed"
    assert occurrence["amount_cents"] == 70000
    assert occurrence["note"] == "Subida de alquiler"

    serve_dashboard.set_projection_status(settings, template_id, "2026-08", "pending")
    occurrence = db.get_projection_occurrence(template_id, "2026-08")
    assert occurrence is not None
    assert occurrence["status"] == "pending"
    assert occurrence["amount_cents"] == 70000


def test_set_projection_status_rejects_bad_input(tmp_path) -> None:
    serve_dashboard = _load_serve_dashboard()
    settings = _settings(tmp_path)
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone, create=True)
    template_id = _template(db)

    with pytest.raises(ValueError):
        serve_dashboard.set_projection_status(settings, template_id, "2026-07", "paid")
    with pytest.raises(KeyError):
        serve_dashboard.set_projection_status(settings, 9999, "2026-07", "completed")
    assert db.get_projection_occurrence(template_id, "2026-07") is None


def test_runtime_status_reports_supervisor_and_pending_files(tmp_path) -> None:
    serve_dashboard = _load_serve_dashboard()
    settings = _settings(tmp_path)
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone, create=True)
    db.add_receipt(
        local_path="ticket.jpg",
        drive_file_id=None,
        drive_url=None,
        telegram_message_id=None,
        caption=None,
        status="pending",
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "runtime_status.json").write_text(
        json.dumps(
            {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "started_at": datetime.now(timezone.utc).isoformat(),
                "supervisor_pid": 100,
                "bot_running": True,
                "bot_pid": 101,
            }
        ),
        encoding="utf-8",
    )

    status = serve_dashboard.runtime_status_payload(settings)

    assert status["supervisor"]["running"] is True
    assert status["bot"]["running"] is True
    assert status["pendingCount"] == 1


def test_transaction_delete_and_restore_http_routes(tmp_path) -> None:
    from http.server import ThreadingHTTPServer
    from threading import Thread
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    module = _load_serve_dashboard()
    settings = _settings(tmp_path)
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone, create=True)
    transaction_id = db.add_manual_transaction(
        kind="expense", amount_cents=990, category="Ocio", note="cine",
        created_at="2026-09-05T12:00:00+02:00",
    )

    class Handler(module.DashboardHandler):
        pass

    Handler.settings = settings
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = "http://127.0.0.1:" + str(server.server_port)

    def call(method: str, path: str, origin: str | None = None) -> int:
        headers = {"Content-Type": "application/json"}
        if origin:
            headers["Origin"] = origin
        request = Request(base + path, data=b"{}", method=method, headers=headers)
        try:
            with urlopen(request) as response:
                return response.status
        except HTTPError as error:
            return error.code

    try:
        # Otro origen no puede borrar datos.
        assert call("DELETE", f"/api/transactions/{transaction_id}", origin="http://evil.test") == 403
        assert db.get_transaction(transaction_id) is not None

        assert call("DELETE", f"/api/transactions/{transaction_id}") == 200
        assert db.get_transaction(transaction_id) is None
        assert call("DELETE", f"/api/transactions/{transaction_id}") == 404

        assert call("POST", f"/api/transactions/{transaction_id}/restore") == 200
        assert db.get_transaction(transaction_id)["note"] == "cine"
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
