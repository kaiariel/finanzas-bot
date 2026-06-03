from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from finance_bot.formatting import (
    fixed_label,
    format_date,
    format_euro,
    format_month,
    parse_created_at,
    tipo_label,
)
from finance_bot.parser import ParsedTransaction


REVIEW_QUEUE_STATUSES = ("nuevo", "pending", "voice_pending", "dudoso")


@dataclass(frozen=True)
class StoredTransaction:
    id: int
    created_at: str
    kind: str
    amount_cents: int
    currency: str
    category: str | None
    note: str
    store: str
    is_fixed: bool
    receipt_drive_url: str | None


class FinanceDatabase:
    def __init__(self, db_path: Path, timezone: str) -> None:
        self.db_path = db_path
        self.timezone = ZoneInfo(timezone)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _now(self) -> str:
        return datetime.now(self.timezone).isoformat(timespec="seconds")

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK (kind IN ('expense', 'income')),
                    amount_cents INTEGER NOT NULL,
                    currency TEXT NOT NULL DEFAULT 'EUR',
                    category TEXT,
                    note TEXT NOT NULL,
                    store TEXT NOT NULL DEFAULT '',
                    is_fixed INTEGER NOT NULL DEFAULT 0,
                    source_text TEXT NOT NULL,
                    receipt_local_path TEXT,
                    receipt_drive_file_id TEXT,
                    receipt_drive_url TEXT,
                    telegram_message_id INTEGER,
                    telegram_user_id INTEGER,
                    telegram_username TEXT,
                    telegram_full_name TEXT
                );

                CREATE TABLE IF NOT EXISTS receipts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    local_path TEXT NOT NULL,
                    drive_file_id TEXT,
                    drive_url TEXT,
                    telegram_message_id INTEGER,
                    caption TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    telegram_user_id INTEGER,
                    telegram_username TEXT,
                    telegram_full_name TEXT
                );

                CREATE TABLE IF NOT EXISTS projection_templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK (kind IN ('expense', 'income')),
                    name TEXT NOT NULL,
                    default_amount_cents INTEGER NOT NULL,
                    category TEXT NOT NULL DEFAULT '',
                    group_name TEXT NOT NULL DEFAULT '',
                    recurrence TEXT NOT NULL DEFAULT 'monthly',
                    start_month TEXT NOT NULL DEFAULT '',
                    installment_current INTEGER,
                    installment_total INTEGER,
                    active INTEGER NOT NULL DEFAULT 1,
                    sort_order INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS projection_occurrences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    template_id INTEGER NOT NULL,
                    month TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'completed', 'skipped')),
                    note TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL,
                    UNIQUE(template_id, month)
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_projection_templates_kind_name
                    ON projection_templates(kind, name);
                """
            )
            self._ensure_column(connection, "transactions", "store", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(
                connection, "transactions", "is_fixed", "INTEGER NOT NULL DEFAULT 0"
            )
            self._ensure_column(connection, "transactions", "telegram_user_id", "INTEGER")
            self._ensure_column(connection, "transactions", "telegram_username", "TEXT")
            self._ensure_column(connection, "transactions", "telegram_full_name", "TEXT")
            self._ensure_column(connection, "transactions", "receipt_id", "INTEGER")
            self._ensure_column(
                connection, "transactions", "review_status", "TEXT NOT NULL DEFAULT 'registered'"
            )
            self._ensure_column(connection, "transactions", "duplicate_of_id", "INTEGER")
            self._ensure_column(connection, "receipts", "telegram_user_id", "INTEGER")
            self._ensure_column(connection, "receipts", "telegram_username", "TEXT")
            self._ensure_column(connection, "receipts", "telegram_full_name", "TEXT")
            self._ensure_column(connection, "receipts", "review_notes", "TEXT")
            self._ensure_column(
                connection,
                "projection_templates",
                "group_name",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                connection,
                "projection_templates",
                "recurrence",
                "TEXT NOT NULL DEFAULT 'monthly'",
            )
            self._ensure_column(
                connection,
                "projection_templates",
                "start_month",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(connection, "projection_templates", "installment_current", "INTEGER")
            self._ensure_column(connection, "projection_templates", "installment_total", "INTEGER")
            self._ensure_column(
                connection, "projection_templates", "active", "INTEGER NOT NULL DEFAULT 1"
            )
            self._ensure_column(
                connection, "projection_templates", "sort_order", "INTEGER NOT NULL DEFAULT 0"
            )

    def _ensure_column(
        self, connection: sqlite3.Connection, table: str, column: str, definition: str
    ) -> None:
        columns = {
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def add_transaction(
        self,
        parsed: ParsedTransaction,
        *,
        receipt_local_path: str | None = None,
        receipt_drive_file_id: str | None = None,
        receipt_drive_url: str | None = None,
        telegram_message_id: int | None = None,
        telegram_user_id: int | None = None,
        telegram_username: str | None = None,
        telegram_full_name: str | None = None,
        receipt_id: int | None = None,
        review_status: str = "registered",
        duplicate_of_id: int | None = None,
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO transactions (
                    created_at, kind, amount_cents, currency, category, note,
                    store, is_fixed, source_text, receipt_local_path, receipt_drive_file_id,
                    receipt_drive_url, telegram_message_id, telegram_user_id,
                    telegram_username, telegram_full_name, receipt_id, review_status,
                    duplicate_of_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._now(),
                    parsed.kind,
                    parsed.amount_cents,
                    parsed.currency,
                    parsed.category,
                    parsed.note,
                    parsed.store,
                    1 if parsed.is_fixed else 0,
                    parsed.source_text,
                    receipt_local_path,
                    receipt_drive_file_id,
                    receipt_drive_url,
                    telegram_message_id,
                    telegram_user_id,
                    telegram_username,
                    telegram_full_name,
                    receipt_id,
                    review_status,
                    duplicate_of_id,
                ),
            )
            return int(cursor.lastrowid)

    def add_manual_transaction(
        self,
        *,
        kind: str,
        amount_cents: int,
        category: str,
        note: str,
        store: str = "",
        is_fixed: bool = False,
        currency: str = "EUR",
        source_text: str = "",
        created_at: str | None = None,
        receipt_local_path: str | None = None,
        receipt_drive_file_id: str | None = None,
        receipt_drive_url: str | None = None,
        telegram_message_id: int | None = None,
        telegram_user_id: int | None = None,
        telegram_username: str | None = None,
        telegram_full_name: str | None = None,
        receipt_id: int | None = None,
        review_status: str = "registered",
        duplicate_of_id: int | None = None,
    ) -> int:
        if kind not in {"expense", "income"}:
            raise ValueError("kind debe ser 'expense' o 'income'")

        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO transactions (
                    created_at, kind, amount_cents, currency, category, note,
                    store, is_fixed, source_text, receipt_local_path, receipt_drive_file_id,
                    receipt_drive_url, telegram_message_id, telegram_user_id,
                    telegram_username, telegram_full_name, receipt_id, review_status,
                    duplicate_of_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    created_at or self._now(),
                    kind,
                    amount_cents,
                    currency,
                    category,
                    note,
                    store,
                    1 if is_fixed else 0,
                    source_text,
                    receipt_local_path,
                    receipt_drive_file_id,
                    receipt_drive_url,
                    telegram_message_id,
                    telegram_user_id,
                    telegram_username,
                    telegram_full_name,
                    receipt_id,
                    review_status,
                    duplicate_of_id,
                ),
            )
            return int(cursor.lastrowid)

    def add_receipt(
        self,
        *,
        local_path: str,
        drive_file_id: str | None,
        drive_url: str | None,
        telegram_message_id: int | None,
        caption: str | None,
        status: str = "pending",
        telegram_user_id: int | None = None,
        telegram_username: str | None = None,
        telegram_full_name: str | None = None,
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO receipts (
                    created_at, local_path, drive_file_id, drive_url,
                    telegram_message_id, caption, status, telegram_user_id,
                    telegram_username, telegram_full_name
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._now(),
                    local_path,
                    drive_file_id,
                    drive_url,
                    telegram_message_id,
                    caption,
                    status,
                    telegram_user_id,
                    telegram_username,
                    telegram_full_name,
                ),
            )
            return int(cursor.lastrowid)

    def get_receipt(self, receipt_id: int) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT id, created_at, local_path, drive_file_id, drive_url,
                       telegram_message_id, caption, status,
                       telegram_user_id, telegram_username, telegram_full_name
                FROM receipts
                WHERE id = ?
                """,
                (receipt_id,),
            ).fetchone()

    def update_receipt_status(
        self, receipt_id: int, status: str, review_notes: str | None = None
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE receipts SET status = ?, review_notes = COALESCE(?, review_notes) WHERE id = ?",
                (status, review_notes, receipt_id),
            )

    def get_transaction(self, transaction_id: int) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT id, created_at, kind, amount_cents, currency, category, note,
                       store, is_fixed, source_text, receipt_local_path,
                       telegram_user_id, telegram_username, telegram_full_name,
                       receipt_id, review_status, duplicate_of_id
                FROM transactions
                WHERE id = ?
                """,
                (transaction_id,),
            ).fetchone()

    def update_transaction(
        self,
        transaction_id: int,
        *,
        created_at: str | None = None,
        kind: str | None = None,
        amount_cents: int | None = None,
        category: str | None = None,
        note: str | None = None,
        store: str | None = None,
        is_fixed: bool | None = None,
    ) -> sqlite3.Row:
        if kind is not None and kind not in {"expense", "income"}:
            raise ValueError("kind debe ser 'expense' o 'income'")

        updates: list[str] = []
        params: list[object] = []
        values = {
            "created_at": created_at,
            "kind": kind,
            "amount_cents": amount_cents,
            "category": category,
            "note": note,
            "store": store,
        }
        for column, value in values.items():
            if value is not None:
                updates.append(f"{column} = ?")
                params.append(value)
        if is_fixed is not None:
            updates.append("is_fixed = ?")
            params.append(1 if is_fixed else 0)

        if not updates:
            existing = self.get_transaction(transaction_id)
            if existing is None:
                raise KeyError(f"No existe el movimiento {transaction_id}")
            return existing

        params.append(transaction_id)
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE transactions SET {', '.join(updates)} WHERE id = ?",
                tuple(params),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"No existe el movimiento {transaction_id}")
            row = connection.execute(
                """
                SELECT id, created_at, kind, amount_cents, currency, category, note,
                       store, is_fixed, source_text, receipt_local_path,
                       telegram_user_id, telegram_username, telegram_full_name,
                       receipt_id, review_status, duplicate_of_id
                FROM transactions
                WHERE id = ?
                """,
                (transaction_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"No existe el movimiento {transaction_id}")
        return row

    def upsert_projection_template(
        self,
        *,
        kind: str,
        name: str,
        default_amount_cents: int,
        category: str,
        group_name: str = "",
        recurrence: str = "monthly",
        start_month: str = "",
        installment_current: int | None = None,
        installment_total: int | None = None,
        active: bool = True,
        sort_order: int = 0,
    ) -> int:
        if kind not in {"expense", "income"}:
            raise ValueError("kind debe ser 'expense' o 'income'")
        if not name.strip():
            raise ValueError("name no puede estar vacio")

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO projection_templates (
                    created_at, kind, name, default_amount_cents, category,
                    group_name, recurrence, start_month, installment_current, installment_total,
                    active, sort_order
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(kind, name) DO UPDATE SET
                    default_amount_cents = excluded.default_amount_cents,
                    category = excluded.category,
                    group_name = excluded.group_name,
                    recurrence = excluded.recurrence,
                    start_month = excluded.start_month,
                    installment_current = excluded.installment_current,
                    installment_total = excluded.installment_total,
                    active = excluded.active,
                    sort_order = excluded.sort_order
                """,
                (
                    self._now(),
                    kind,
                    name.strip(),
                    default_amount_cents,
                    category,
                    group_name,
                    recurrence,
                    start_month,
                    installment_current,
                    installment_total,
                    1 if active else 0,
                    sort_order,
                ),
            )
            row = connection.execute(
                "SELECT id FROM projection_templates WHERE kind = ? AND name = ?",
                (kind, name.strip()),
            ).fetchone()
        if row is None:
            raise RuntimeError("No se pudo guardar la proyeccion")
        return int(row["id"])

    def update_projection_template(
        self,
        template_id: int,
        *,
        name: str | None = None,
        kind: str | None = None,
        default_amount_cents: int | None = None,
        category: str | None = None,
        start_month: str | None = None,
        installment_current: int | None = None,
        installment_total: int | None = None,
        clear_installments: bool = False,
        active: bool | None = None,
    ) -> sqlite3.Row:
        if kind is not None and kind not in {"expense", "income"}:
            raise ValueError("kind debe ser 'expense' o 'income'")

        updates: list[str] = []
        params: list[object] = []
        values = {
            "kind": kind,
            "name": name.strip() if name is not None else None,
            "default_amount_cents": default_amount_cents,
            "category": category,
            "start_month": start_month,
        }
        for column, value in values.items():
            if value is not None:
                if column == "name" and value == "":
                    raise ValueError("name no puede estar vacio")
                updates.append(f"{column} = ?")
                params.append(value)
        if clear_installments:
            updates.extend(["installment_current = ?", "installment_total = ?"])
            params.extend([None, None])
        else:
            if installment_current is not None:
                updates.append("installment_current = ?")
                params.append(installment_current)
            if installment_total is not None:
                updates.append("installment_total = ?")
                params.append(installment_total)
        if active is not None:
            updates.append("active = ?")
            params.append(1 if active else 0)

        if not updates:
            row = self.get_projection_template(template_id)
            if row is None:
                raise KeyError(f"No existe la proyeccion {template_id}")
            return row

        params.append(template_id)
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE projection_templates SET {', '.join(updates)} WHERE id = ?",
                tuple(params),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"No existe la proyeccion {template_id}")
            row = connection.execute(
                """
                SELECT id, created_at, kind, name, default_amount_cents, category,
                       group_name, recurrence, start_month, installment_current, installment_total,
                       active, sort_order
                FROM projection_templates
                WHERE id = ?
                """,
                (template_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"No existe la proyeccion {template_id}")
        return row

    def get_projection_template(self, template_id: int) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT id, created_at, kind, name, default_amount_cents, category,
                       group_name, recurrence, start_month, installment_current, installment_total,
                       active, sort_order
                FROM projection_templates
                WHERE id = ?
                """,
                (template_id,),
            ).fetchone()

    def set_projection_occurrence(
        self,
        *,
        template_id: int,
        month: str,
        amount_cents: int,
        status: str = "pending",
        note: str = "",
    ) -> int:
        if status not in {"pending", "completed", "skipped"}:
            raise ValueError("status debe ser pending, completed o skipped")
        if not self.get_projection_template(template_id):
            raise KeyError(f"No existe la proyeccion {template_id}")

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO projection_occurrences (
                    template_id, month, amount_cents, status, note, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(template_id, month) DO UPDATE SET
                    amount_cents = excluded.amount_cents,
                    status = excluded.status,
                    note = excluded.note,
                    updated_at = excluded.updated_at
                """,
                (template_id, month, amount_cents, status, note, self._now()),
            )
            row = connection.execute(
                """
                SELECT id FROM projection_occurrences
                WHERE template_id = ? AND month = ?
                """,
                (template_id, month),
            ).fetchone()
        if row is None:
            raise RuntimeError("No se pudo guardar el mes proyectado")
        return int(row["id"])

    def get_projection_occurrence(self, template_id: int, month: str) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT id, template_id, month, amount_cents, status, note, updated_at
                FROM projection_occurrences
                WHERE template_id = ? AND month = ?
                """,
                (template_id, month),
            ).fetchone()

    def list_projection_templates(self, active_only: bool = True) -> list[sqlite3.Row]:
        where = "WHERE active = 1" if active_only else ""
        with self._connect() as connection:
            return connection.execute(
                f"""
                SELECT id, created_at, kind, name, default_amount_cents, category,
                       group_name, recurrence, start_month, installment_current, installment_total,
                       active, sort_order
                FROM projection_templates
                {where}
                ORDER BY kind DESC, sort_order ASC, name ASC
                """
            ).fetchall()

    def list_projection_occurrences(
        self, start_month: str | None = None, end_month: str | None = None
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        params: list[object] = []
        if start_month:
            clauses.append("month >= ?")
            params.append(start_month)
        if end_month:
            clauses.append("month <= ?")
            params.append(end_month)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as connection:
            return connection.execute(
                f"""
                SELECT id, template_id, month, amount_cents, status, note, updated_at
                FROM projection_occurrences
                {where}
                ORDER BY month ASC, template_id ASC
                """,
                tuple(params),
            ).fetchall()

    def find_possible_duplicate(
        self,
        *,
        kind: str,
        amount_cents: int,
        category: str,
        store: str = "",
        created_at: str,
        exclude_id: int | None = None,
    ) -> sqlite3.Row | None:
        day_prefix = created_at[:10]
        params: list[object] = [day_prefix, kind, amount_cents, category, store or ""]
        exclude_clause = ""
        if exclude_id is not None:
            exclude_clause = "AND id != ?"
            params.append(exclude_id)

        with self._connect() as connection:
            return connection.execute(
                f"""
                SELECT id, created_at, kind, amount_cents, category, note, store
                FROM transactions
                WHERE substr(created_at, 1, 10) = ?
                  AND kind = ?
                  AND amount_cents = ?
                  AND category = ?
                  AND COALESCE(store, '') = ?
                  {exclude_clause}
                ORDER BY id ASC
                LIMIT 1
                """,
                tuple(params),
            ).fetchone()

    def summary_current_month(self) -> dict[str, int]:
        now = datetime.now(self.timezone)
        month_prefix = now.strftime("%Y-%m")
        return self.summary_for_period(month_prefix=month_prefix)

    def summary_for_period(self, month_prefix: str | None = None) -> dict[str, int]:
        where = ""
        params: tuple[str, ...] = ()
        if month_prefix:
            where = "WHERE substr(created_at, 1, 7) = ?"
            params = (month_prefix,)

        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT kind, COALESCE(SUM(amount_cents), 0) AS total, COUNT(*) AS count
                FROM transactions
                {where}
                GROUP BY kind
                """,
                params,
            ).fetchall()

        summary = {"expense_cents": 0, "income_cents": 0, "expense_count": 0, "income_count": 0}
        for row in rows:
            prefix = "expense" if row["kind"] == "expense" else "income"
            summary[f"{prefix}_cents"] = int(row["total"])
            summary[f"{prefix}_count"] = int(row["count"])
        return summary

    def status_overview(self) -> dict[str, int]:
        month = self.summary_current_month()
        total = self.summary_for_period()
        pending_count = len(self.list_pending_files())
        return {
            "month_income_cents": month["income_cents"],
            "month_expense_cents": month["expense_cents"],
            "month_income_count": month["income_count"],
            "month_expense_count": month["expense_count"],
            "total_income_cents": total["income_cents"],
            "total_expense_cents": total["expense_cents"],
            "total_income_count": total["income_count"],
            "total_expense_count": total["expense_count"],
            "pending_count": pending_count,
        }

    def list_pending_files(self) -> list[sqlite3.Row]:
        placeholders = ", ".join("?" for _ in REVIEW_QUEUE_STATUSES)
        with self._connect() as connection:
            return connection.execute(
                f"""
                SELECT id, created_at, local_path, telegram_message_id, caption, status,
                       telegram_user_id, telegram_username, telegram_full_name, review_notes
                FROM receipts
                WHERE status IN ({placeholders})
                ORDER BY created_at ASC, id ASC
                """,
                REVIEW_QUEUE_STATUSES,
            ).fetchall()

    def list_receipts(self) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT id, created_at, local_path, telegram_message_id, caption, status,
                       telegram_user_id, telegram_username, telegram_full_name, review_notes
                FROM receipts
                ORDER BY created_at ASC, id ASC
                """
            ).fetchall()

    def list_transactions(self) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT id, created_at, kind, amount_cents, currency, category, note,
                       store, is_fixed, source_text, receipt_local_path,
                       telegram_user_id, telegram_username, telegram_full_name,
                       receipt_id, review_status, duplicate_of_id
                FROM transactions
                ORDER BY created_at ASC, id ASC
                """
            ).fetchall()

    def export_csv(self, csv_path: Path) -> Path:
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT created_at, kind, amount_cents, category, note, store, is_fixed
                FROM transactions
                ORDER BY created_at ASC, id ASC
                """
            ).fetchall()

        with csv_path.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(
                [
                    "Mes",
                    "Fecha",
                    "Descripción",
                    "Categoría",
                    "Cantidad",
                    "Tipo",
                    "Tienda",
                    "Es fijo",
                ]
            )
            for row in rows:
                created_at = parse_created_at(row["created_at"])
                writer.writerow(
                    [
                        format_month(created_at),
                        format_date(created_at),
                        row["note"],
                        row["category"] or "",
                        format_euro(row["amount_cents"]),
                        tipo_label(row["kind"]),
                        row["store"] or "",
                        fixed_label(bool(row["is_fixed"])),
                    ]
                )
        return csv_path
