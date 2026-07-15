from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from finance_bot.db import FinanceDatabase


def test_recover_missing_receipts_relinks_from_recovery_folder(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    db_path = data_dir / "finances.db"
    report_path = tmp_path / "reports" / "finanzas.html"
    receipt_dir = data_dir / "receipts"
    recovery_dir = data_dir / "receipts-recovery"
    voice_dir = data_dir / "voices"

    db = FinanceDatabase(db_path, "Europe/Madrid")
    missing_name = "ticket-20260713-132500-AQADHA5rG_qVqVJ-.jpg"
    receipt_id = db.add_receipt(
        local_path=str(Path(r"G:\Mi unidad\Finanzas - Tickets\2026-07 Julio") / missing_name),
        drive_file_id=None,
        drive_url=None,
        telegram_message_id=238,
        caption=None,
        status="missing",
    )

    recovery_dir.mkdir(parents=True, exist_ok=True)
    recovered_file = recovery_dir / missing_name
    recovered_file.write_bytes(b"fake image bytes")

    env = os.environ.copy()
    env.update(
        {
            "DATA_DIR": str(data_dir),
            "SQLITE_DB_PATH": str(db_path),
            "REPORT_HTML_PATH": str(report_path),
            "RECEIPTS_SYNC_DIR": str(receipt_dir),
            "VOICES_SYNC_DIR": str(voice_dir),
            "TIMEZONE": "Europe/Madrid",
        }
    )

    command = [
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "scripts" / "recover_missing_receipts.py"),
    ]
    result = subprocess.run(command, capture_output=True, text=True, env=env, check=False)

    assert result.returncode == 0
    assert f"#{receipt_id} recuperado" in result.stdout

    receipt = db.get_receipt(receipt_id)
    assert receipt is not None
    assert receipt["status"] == "pending"
    assert receipt["local_path"] == str(recovered_file)
