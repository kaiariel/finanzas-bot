from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from finance_bot.db import FinanceDatabase


def test_register_manual_entries_rejects_invalid_payload_without_partial_writes(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "finances.db"
    report_path = tmp_path / "finanzas.html"
    csv_path = tmp_path / "movimientos.csv"
    receipt_dir = tmp_path / "receipts"
    voice_dir = tmp_path / "voices"

    db = FinanceDatabase(db_path, "Europe/Madrid", create=True)
    receipt_id = db.add_receipt(
        local_path=str(receipt_dir / "ticket.jpg"),
        drive_file_id=None,
        drive_url=None,
        telegram_message_id=1,
        caption="ticket pendiente",
        status="pending",
        telegram_user_id=1,
        telegram_username="tester",
        telegram_full_name="Tester",
    )

    payload_path = tmp_path / "entradas.json"
    payload_path.write_text(
        json.dumps(
            {
                "receipt_id": receipt_id,
                "entries": [
                    {
                        "type": "Egreso",
                        "amount": 15,
                        "category": "Hogar",
                        "description": "Bolsa reutilizable",
                        "date": "2026-06-15",
                    },
                    {
                        "type": "Egreso",
                        "amount": 230,
                        "category": "Categoria invalida",
                        "description": "Caldo pollo",
                        "date": "2026-06-15",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.update(
        {
            "DATA_DIR": str(tmp_path / "data"),
            "SQLITE_DB_PATH": str(db_path),
            "EXPORT_CSV_PATH": str(csv_path),
            "REPORT_HTML_PATH": str(report_path),
            "RECEIPTS_SYNC_DIR": str(receipt_dir),
            "VOICES_SYNC_DIR": str(voice_dir),
            "TIMEZONE": "Europe/Madrid",
            # Fija la salida del script para no depender de la consola que lance pytest.
            "PYTHONIOENCODING": "utf-8",
        }
    )

    command = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "scripts" / "register_manual_entries.py"),
        str(payload_path),
    ]
    # encoding explicito: los scripts escriben en UTF-8 aunque la consola sea cp1252.
    result = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", env=env, check=False
    )

    assert result.returncode != 0
    assert "Categoría no válida" in result.stderr or "Categoría no válida" in result.stdout

    rows = db.list_transactions()
    assert rows == []
    receipt = db.get_receipt(receipt_id)
    assert receipt is not None
    assert receipt["status"] == "pending"
