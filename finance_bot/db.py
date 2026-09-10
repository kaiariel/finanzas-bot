from __future__ import annotations

import csv
import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
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
from finance_bot.parser import (
    HOUSEHOLD_FOOD_CATEGORY,
    LEGACY_HOUSEHOLD_FOOD_CATEGORIES,
    ParsedTransaction,
    VALID_CATEGORIES,
)


REVIEW_QUEUE_STATUSES = ("nuevo", "pending", "voice_pending", "dudoso", "missing")


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

    def log_audit(self, action: str, entity_type: str, entity_id: int | None, details: str = "") -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO audit_log(created_at, action, entity_type, entity_id, details) VALUES (?, ?, ?, ?, ?)",
                (self._now(), action, entity_type, entity_id, details),
            )

    def undo_last_transaction_change(self, transaction_id: int) -> sqlite3.Row:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, details FROM audit_log WHERE entity_type = 'transaction' AND entity_id = ? AND action = 'update' ORDER BY id DESC LIMIT 1",
                (transaction_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"No hay cambios que deshacer para el movimiento {transaction_id}")
            payload = json.loads(row["details"])
            before = payload.get("before") or {}
            columns = ["created_at", "kind", "amount_cents", "category", "note", "store", "is_fixed", "review_status", "inference_notes", "projection_template_id", "account_id", "due_date", "paid_amount_cents"]
            available = [column for column in columns if column in before]
            connection.execute("UPDATE transactions SET " + ", ".join(f"{column} = ?" for column in available) + " WHERE id = ?", tuple(before[column] for column in available) + (transaction_id,))
            connection.execute("INSERT INTO audit_log(created_at, action, entity_type, entity_id, details) VALUES (?, 'undo', 'transaction', ?, ?)", (self._now(), transaction_id, json.dumps({"undid": row["id"]})))
            restored = connection.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
        if restored is None:
            raise KeyError(f"No existe el movimiento {transaction_id}")
        return restored

    def set_transaction_payment(self, transaction_id: int, paid_amount_cents: int, due_date: str | None = None) -> sqlite3.Row:
        if paid_amount_cents < 0:
            raise ValueError("El importe pagado no puede ser negativo")
        with self._connect() as connection:
            connection.execute("UPDATE transactions SET paid_amount_cents = ?, due_date = ? WHERE id = ?", (paid_amount_cents, due_date, transaction_id))
            row = connection.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
        if row is None:
            raise KeyError(f"No existe el movimiento {transaction_id}")
        self.log_audit("payment", "transaction", transaction_id, f"Pagado {paid_amount_cents}")
        return row

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
                    end_month TEXT NOT NULL DEFAULT '',
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

                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    action TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id INTEGER,
                    details TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    name TEXT NOT NULL UNIQUE,
                    account_type TEXT NOT NULL DEFAULT 'bank',
                    opening_balance_cents INTEGER NOT NULL DEFAULT 0,
                    currency TEXT NOT NULL DEFAULT 'EUR',
                    active INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS transfers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    from_account_id INTEGER NOT NULL,
                    to_account_id INTEGER NOT NULL,
                    amount_cents INTEGER NOT NULL,
                    note TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(from_account_id) REFERENCES accounts(id),
                    FOREIGN KEY(to_account_id) REFERENCES accounts(id)
                );

                CREATE TABLE IF NOT EXISTS budgets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    month TEXT NOT NULL,
                    category TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL,
                    UNIQUE(month, category)
                );

                CREATE TABLE IF NOT EXISTS savings_goals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    name TEXT NOT NULL,
                    target_cents INTEGER NOT NULL,
                    current_cents INTEGER NOT NULL DEFAULT 0,
                    target_date TEXT,
                    wallet_id INTEGER,
                    active INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS savings_settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    current_balance_cents INTEGER NOT NULL DEFAULT 0,
                    current_balance_date TEXT NOT NULL DEFAULT '',
                    include_current_balance INTEGER NOT NULL DEFAULT 0,
                    emergency_monthly_cents INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS savings_wallets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    name TEXT NOT NULL UNIQUE,
                    balance_cents INTEGER NOT NULL DEFAULT 0,
                    include_in_projection INTEGER NOT NULL DEFAULT 1,
                    active INTEGER NOT NULL DEFAULT 1
                );

                CREATE TABLE IF NOT EXISTS import_batches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'preview',
                    row_count INTEGER NOT NULL DEFAULT 0
                );
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
            self._ensure_column(
                connection, "transactions", "inference_notes", "TEXT NOT NULL DEFAULT '[]'"
            )
            self._ensure_column(connection, "transactions", "projection_template_id", "INTEGER")
            self._ensure_column(connection, "transactions", "account_id", "INTEGER")
            self._ensure_column(connection, "transactions", "transfer_id", "INTEGER")
            self._ensure_column(connection, "transactions", "due_date", "TEXT")
            self._ensure_column(connection, "transactions", "paid_amount_cents", "INTEGER")
            self._ensure_column(connection, "receipts", "telegram_user_id", "INTEGER")
            self._ensure_column(connection, "receipts", "telegram_username", "TEXT")
            self._ensure_column(connection, "receipts", "telegram_full_name", "TEXT")
            self._ensure_column(connection, "receipts", "review_notes", "TEXT")
            self._ensure_column(connection, "savings_goals", "wallet_id", "INTEGER")
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
            self._ensure_column(
                connection,
                "projection_templates",
                "end_month",
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
            self._apply_household_food_projection_migration(connection)

    def _ensure_column(
        self, connection: sqlite3.Connection, table: str, column: str, definition: str
    ) -> None:
        columns = {
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _apply_household_food_projection_migration(self, connection: sqlite3.Connection) -> None:
        legacy_categories = tuple(
            dict.fromkeys((*LEGACY_HOUSEHOLD_FOOD_CATEGORIES, HOUSEHOLD_FOOD_CATEGORY))
        )
        placeholders = ", ".join("?" for _ in legacy_categories)
        connection.execute(
            f"UPDATE transactions SET category = ? WHERE category IN ({placeholders})",
            (HOUSEHOLD_FOOD_CATEGORY, *legacy_categories),
        )
        connection.execute(
            f"""
            UPDATE projection_templates
            SET category = ?
            WHERE kind = 'expense' AND category IN ({placeholders})
            """,
            (HOUSEHOLD_FOOD_CATEGORY, *legacy_categories),
        )

        household_names = (
            "Hogar/Alimentacion",
            "Hogar/Alimentación",
            "Hogar y Alimentacion",
            HOUSEHOLD_FOOD_CATEGORY,
        )
        household_placeholders = ", ".join("?" for _ in household_names)
        connection.execute(
            f"""
            UPDATE projection_templates
            SET name = ?, category = ?
            WHERE kind = 'expense' AND name IN ({household_placeholders})
            """,
            (HOUSEHOLD_FOOD_CATEGORY, HOUSEHOLD_FOOD_CATEGORY, *household_names),
        )

        izhan_projection_names = ("Musica Izhan", "Música Izhan", "Izhan hijo")
        izhan_placeholders = ", ".join("?" for _ in izhan_projection_names)
        connection.execute(
            f"""
            UPDATE projection_templates
            SET active = 0
            WHERE kind = 'expense' AND name IN ({izhan_placeholders})
            """,
            izhan_projection_names,
        )
        connection.execute(
            """
            UPDATE projection_templates
            SET category = 'Ingresos laborales'
            WHERE kind = 'income'
              AND name IN ('Sueldo', 'Sueldo cocina Ariel', 'Sueldo Dahiana')
              AND category = ?
            """,
            (HOUSEHOLD_FOOD_CATEGORY,),
        )

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
        account_id: int | None = None,
        transfer_id: int | None = None,
        due_date: str | None = None,
        paid_amount_cents: int | None = None,
    ) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO transactions (
                    created_at, kind, amount_cents, currency, category, note,
                    store, is_fixed, source_text, receipt_local_path, receipt_drive_file_id,
                    receipt_drive_url, telegram_message_id, telegram_user_id,
                    telegram_username, telegram_full_name, receipt_id, review_status,
                    duplicate_of_id, inference_notes, account_id, transfer_id, due_date, paid_amount_cents
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    json.dumps(list(parsed.inference_notes), ensure_ascii=False),
                    account_id,
                    transfer_id,
                    due_date,
                    paid_amount_cents if paid_amount_cents is not None else parsed.amount_cents,
                ),
            )
            transaction_id = int(cursor.lastrowid)
        self.auto_apply_projection_for_transaction(transaction_id)
        return transaction_id

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
        account_id: int | None = None,
        transfer_id: int | None = None,
        due_date: str | None = None,
        paid_amount_cents: int | None = None,
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
                    duplicate_of_id, inference_notes, account_id, transfer_id, due_date, paid_amount_cents
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    "[]",
                    account_id,
                    transfer_id,
                    due_date,
                    paid_amount_cents if paid_amount_cents is not None else amount_cents,
                ),
            )
            transaction_id = int(cursor.lastrowid)
        self.auto_apply_projection_for_transaction(transaction_id)
        return transaction_id

    def create_account(self, name: str, account_type: str = "bank", opening_balance_cents: int = 0) -> int:
        name = name.strip()
        if not name:
            raise ValueError("El nombre de la cuenta es obligatorio")
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO accounts(created_at, name, account_type, opening_balance_cents) VALUES (?, ?, ?, ?)",
                (self._now(), name, account_type, int(opening_balance_cents)),
            )
            return int(cursor.lastrowid)

    def list_accounts(self, active_only: bool = True) -> list[sqlite3.Row]:
        query = "SELECT * FROM accounts"
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY name"
        with self._connect() as connection:
            return connection.execute(query).fetchall()

    def add_transfer(self, *, from_account_id: int, to_account_id: int, amount_cents: int, note: str = "", created_at: str | None = None) -> int:
        if from_account_id == to_account_id or amount_cents <= 0:
            raise ValueError("La transferencia debe unir dos cuentas distintas y tener importe positivo")
        with self._connect() as connection:
            account_ids = {row[0] for row in connection.execute("SELECT id FROM accounts WHERE active = 1 AND id IN (?, ?)", (from_account_id, to_account_id)).fetchall()}
            if account_ids != {from_account_id, to_account_id}:
                raise ValueError("La cuenta de origen o destino no existe")
            cursor = connection.execute(
                "INSERT INTO transfers(created_at, from_account_id, to_account_id, amount_cents, note) VALUES (?, ?, ?, ?, ?)",
                (created_at or self._now(), from_account_id, to_account_id, amount_cents, note),
            )
            return int(cursor.lastrowid)

    def account_balances(self) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT a.*, a.opening_balance_cents
                    + COALESCE((SELECT SUM(t.amount_cents) FROM transfers t WHERE t.to_account_id = a.id), 0)
                    - COALESCE((SELECT SUM(t.amount_cents) FROM transfers t WHERE t.from_account_id = a.id), 0)
                    + COALESCE((SELECT SUM(CASE WHEN x.kind = 'income' THEN x.amount_cents ELSE -x.amount_cents END) FROM transactions x WHERE x.account_id = a.id), 0)
                    AS balance_cents
                FROM accounts a WHERE a.active = 1 ORDER BY a.name
                """
            ).fetchall()

    def upsert_budget(self, month: str, category: str, amount_cents: int) -> None:
        if amount_cents < 0:
            raise ValueError("El presupuesto no puede ser negativo")
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO budgets(month, category, amount_cents) VALUES (?, ?, ?) ON CONFLICT(month, category) DO UPDATE SET amount_cents = excluded.amount_cents",
                (month, category, amount_cents),
            )

    def list_budgets(self, month: str | None = None) -> list[sqlite3.Row]:
        with self._connect() as connection:
            if month:
                return connection.execute("SELECT * FROM budgets WHERE month = ? ORDER BY category", (month,)).fetchall()
            return connection.execute("SELECT * FROM budgets ORDER BY month, category").fetchall()

    def create_savings_goal(self, name: str, target_cents: int, target_date: str | None = None, wallet_id: int | None = None) -> int:
        if target_cents <= 0:
            raise ValueError("El objetivo debe ser positivo")
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO savings_goals(created_at, name, target_cents, target_date, wallet_id) VALUES (?, ?, ?, ?, ?)",
                (self._now(), name.strip(), target_cents, target_date, wallet_id),
            )
            return int(cursor.lastrowid)

    def list_savings_goals(self, active_only: bool = True) -> list[sqlite3.Row]:
        query = "SELECT * FROM savings_goals" + (" WHERE active = 1" if active_only else "") + " ORDER BY target_date, name"
        with self._connect() as connection:
            return connection.execute(query).fetchall()

    def get_savings_settings(self) -> sqlite3.Row:
        with self._connect() as connection:
            connection.execute("INSERT OR IGNORE INTO savings_settings(id) VALUES (1)")
            return connection.execute("SELECT * FROM savings_settings WHERE id = 1").fetchone()

    def update_savings_settings(self, *, current_balance_cents: int, current_balance_date: str, include_current_balance: bool, emergency_monthly_cents: int) -> None:
        if current_balance_cents < 0 or emergency_monthly_cents < 0:
            raise ValueError("Los importes de ahorro no pueden ser negativos")
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO savings_settings(id, current_balance_cents, current_balance_date, include_current_balance, emergency_monthly_cents) VALUES (1, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET current_balance_cents=excluded.current_balance_cents, current_balance_date=excluded.current_balance_date, include_current_balance=excluded.include_current_balance, emergency_monthly_cents=excluded.emergency_monthly_cents",
                (current_balance_cents, current_balance_date, 1 if include_current_balance else 0, emergency_monthly_cents),
            )
        self.log_audit("update", "savings_settings", 1, "Saldo y reserva actualizados")

    def create_savings_wallet(self, name: str, balance_cents: int, include_in_projection: bool = True) -> int:
        if not name.strip() or balance_cents < 0:
            raise ValueError("La cartera necesita nombre e importe válido")
        with self._connect() as connection:
            cursor = connection.execute("INSERT INTO savings_wallets(created_at, name, balance_cents, include_in_projection) VALUES (?, ?, ?, ?)", (self._now(), name.strip(), balance_cents, 1 if include_in_projection else 0))
            wallet_id = int(cursor.lastrowid)
        self.log_audit("create", "savings_wallet", wallet_id, "Cartera creada")
        return wallet_id

    def update_savings_wallet(self, wallet_id: int, *, name: str, balance_cents: int, include_in_projection: bool, active: bool = True) -> None:
        if not name.strip() or balance_cents < 0:
            raise ValueError("La cartera necesita nombre e importe válido")
        with self._connect() as connection:
            cursor = connection.execute("UPDATE savings_wallets SET name = ?, balance_cents = ?, include_in_projection = ?, active = ? WHERE id = ?", (name.strip(), balance_cents, 1 if include_in_projection else 0, 1 if active else 0, wallet_id))
            if cursor.rowcount == 0:
                raise KeyError(f"No existe la cartera {wallet_id}")
        self.log_audit("update", "savings_wallet", wallet_id, "Cartera actualizada")

    def list_savings_wallets(self, active_only: bool = True) -> list[sqlite3.Row]:
        with self._connect() as connection:
            return connection.execute("SELECT * FROM savings_wallets " + ("WHERE active = 1 " if active_only else "") + "ORDER BY name").fetchall()

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

    def register_receipt_entries(self, receipt_id: int, entries: list[dict[str, object]], *, allow_existing: bool = False) -> list[int]:
        receipt = self.get_receipt(receipt_id)
        if receipt is None:
            raise KeyError(f"No existe el ticket {receipt_id}")
        if not entries:
            raise ValueError("El ticket necesita al menos una línea")
        if not allow_existing:
            with self._connect() as connection:
                if connection.execute("SELECT 1 FROM transactions WHERE receipt_id = ? LIMIT 1", (receipt_id,)).fetchone():
                    raise ValueError("El ticket ya tiene movimientos; activa allow_existing para reemplazarlos")
        prepared: list[tuple[object, ...]] = []
        for entry in entries:
            kind = str(entry.get("kind") or "expense")
            amount = int(entry.get("amount_cents") or 0)
            category = str(entry.get("category") or "")
            note = str(entry.get("note") or "").strip()
            if kind not in {"expense", "income"} or amount <= 0 or category not in VALID_CATEGORIES or not note:
                raise ValueError("Cada línea necesita tipo, importe, categoría y descripción")
            prepared.append((kind, amount, category, note, str(entry.get("store") or ""), bool(entry.get("is_fixed")), entry.get("created_at")))
        created_ids: list[int] = []
        with self._connect() as connection:
            for kind, amount, category, note, store, is_fixed, created_at in prepared:
                cursor = connection.execute(
                    """INSERT INTO transactions(created_at, kind, amount_cents, currency, category, note, store, is_fixed, source_text, receipt_local_path, telegram_message_id, telegram_user_id, telegram_username, telegram_full_name, receipt_id, review_status, inference_notes, paid_amount_cents)
                       VALUES (?, ?, ?, 'EUR', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'registered', '[]', ?)""",
                    (created_at or self._now(), kind, amount, category, note, store, int(is_fixed), receipt["caption"] or "", receipt["local_path"], receipt["telegram_message_id"], receipt["telegram_user_id"], receipt["telegram_username"], receipt["telegram_full_name"], receipt_id, amount),
                )
                created_ids.append(int(cursor.lastrowid))
            connection.execute("UPDATE receipts SET status = 'processed', review_notes = COALESCE(review_notes, '') WHERE id = ?", (receipt_id,))
        for transaction_id in created_ids:
            self.auto_apply_projection_for_transaction(transaction_id)
        return created_ids

    def get_transaction(self, transaction_id: int) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT *
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
        review_status: str | None = None,
        inference_notes: list[str] | tuple[str, ...] | None = None,
        projection_template_id: int | None = None,
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
            "review_status": review_status,
        }
        for column, value in values.items():
            if value is not None:
                updates.append(f"{column} = ?")
                params.append(value)
        if is_fixed is not None:
            updates.append("is_fixed = ?")
            params.append(1 if is_fixed else 0)
        if inference_notes is not None:
            updates.append("inference_notes = ?")
            params.append(json.dumps(list(inference_notes), ensure_ascii=False))
        if projection_template_id is not None:
            updates.append("projection_template_id = ?")
            params.append(projection_template_id)

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
                SELECT *
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
        end_month: str = "",
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
                    group_name, recurrence, start_month, end_month, installment_current, installment_total,
                    active, sort_order
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(kind, name) DO UPDATE SET
                    default_amount_cents = excluded.default_amount_cents,
                    category = excluded.category,
                    group_name = excluded.group_name,
                    recurrence = excluded.recurrence,
                    start_month = excluded.start_month,
                    end_month = excluded.end_month,
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
                    end_month,
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
        end_month: str | None = None,
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
            "end_month": end_month,
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
                       group_name, recurrence, start_month, end_month, installment_current, installment_total,
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
                       group_name, recurrence, start_month, end_month, installment_current, installment_total,
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

    def end_projection_from(self, template_id: int, month: str) -> sqlite3.Row:
        """Finish a recurring projection from ``month`` while preserving prior months."""
        template = self.get_projection_template(template_id)
        if template is None:
            raise KeyError(f"No existe la proyeccion {template_id}")
        start_month = str(template["start_month"] or month)
        if month < start_month:
            raise ValueError("El mes final no puede ser anterior al inicio")

        if month == start_month:
            with self._connect() as connection:
                connection.execute("UPDATE projection_templates SET active = 0, end_month = ? WHERE id = ?", (month, template_id))
                connection.execute(
                    "UPDATE projection_occurrences SET status = 'skipped', note = ?, updated_at = ? WHERE template_id = ? AND month >= ?",
                    ("Finalizado desde este mes", self._now(), template_id, month),
                )
        else:
            current = datetime.strptime(month, "%Y-%m")
            previous = (current.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
            with self._connect() as connection:
                connection.execute("UPDATE projection_templates SET active = 1, end_month = ? WHERE id = ?", (previous, template_id))
                connection.execute(
                    "UPDATE projection_occurrences SET status = 'skipped', note = ?, updated_at = ? WHERE template_id = ? AND month >= ?",
                    ("Finalizado desde este mes", self._now(), template_id, month),
                )
        self.log_audit("end", "projection", template_id, f"Finalizada desde {month}")
        updated = self.get_projection_template(template_id)
        if updated is None:
            raise KeyError(f"No existe la proyeccion {template_id}")
        return updated

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
                       group_name, recurrence, start_month, end_month, installment_current, installment_total,
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
                SELECT *
                FROM transactions
                ORDER BY created_at ASC, id ASC
                """
            ).fetchall()

    def auto_apply_projection_for_transaction(self, transaction_id: int) -> int | None:
        row = self.get_transaction(transaction_id)
        if row is None:
            raise KeyError(f"No existe el movimiento {transaction_id}")

        month = str(row["created_at"])[:7]
        transaction_text = " ".join(
            part
            for part in (
                row["note"],
                row["source_text"],
                row["store"],
                row["category"],
            )
            if part
        )
        normalized_text = self._normalize_text(transaction_text)
        templates = [
            template
            for template in self.list_projection_templates()
            if template["kind"] == row["kind"] and self._projection_applies_to_month(template, month)
        ]
        if not templates:
            return None

        scored: list[tuple[int, int, sqlite3.Row]] = []
        for template in templates:
            score = self._projection_match_score(row, template, normalized_text, month)
            if score > 0:
                scored.append((score, int(template["id"]), template))
        if not scored:
            return None

        scored.sort(key=lambda item: (item[0], -item[1]), reverse=True)
        best_score, _, best_template = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else -999
        if best_score < 70 or best_score - second_score < 12:
            return None

        template_id = int(best_template["id"])
        occurrence = self.get_projection_occurrence(template_id, month)
        amount_cents = (
            int(occurrence["amount_cents"])
            if occurrence is not None
            else int(best_template["default_amount_cents"])
        )
        note = str(occurrence["note"]) if occurrence is not None else ""
        if not note.strip():
            note = f"Marcado automaticamente por movimiento #{transaction_id}: {row['note']}"
        self.set_projection_occurrence(
            template_id=template_id,
            month=month,
            amount_cents=amount_cents,
            status="completed",
            note=note,
        )
        self.update_transaction(transaction_id, projection_template_id=template_id)
        return template_id

    def _projection_applies_to_month(self, template: sqlite3.Row, month: str) -> bool:
        start_month = str(template["start_month"] or month)
        month_offset = self._month_distance(start_month, month)
        if month_offset < 0:
            return False
        end_month = str(template["end_month"] or "")
        if end_month and month > end_month:
            return False
        installment_current = template["installment_current"]
        installment_total = template["installment_total"]
        if installment_current and installment_total:
            return int(installment_current) + month_offset <= int(installment_total)
        return True

    def _projection_match_score(
        self,
        transaction: sqlite3.Row,
        template: sqlite3.Row,
        normalized_text: str,
        month: str,
    ) -> int:
        template_name = self._normalize_text(str(template["name"] or ""))
        if not template_name:
            return 0

        template_tokens = set(re.findall(r"\w+", template_name))
        text_tokens = set(re.findall(r"\w+", normalized_text))
        # A substring is not a concept: agua must not match Paraguay and
        # a short label such as Cu must not match cuota/cuidado.
        meaningful = {token for token in template_tokens if len(token) >= 3}
        if not meaningful or not meaningful.intersection(text_tokens):
            return 0
        if len(meaningful) == 1 and self._normalize_text(str(transaction["category"] or "")) != self._normalize_text(str(template["category"] or "")):
            return 0
        overlap = len(template_tokens & text_tokens)
        score = overlap * 18

        if re.search(r"(?<!\w)" + re.escape(template_name) + r"(?!\w)", normalized_text):
            score += 80
        if normalized_text and normalized_text in template_name:
            score += 35
        if transaction["store"]:
            store = self._normalize_text(str(transaction["store"]))
            if store and (store in template_name or template_name in store):
                score += 24
        if self._normalize_text(str(transaction["category"] or "")) == self._normalize_text(
            str(template["category"] or "")
        ):
            score += 16

        template_amount = int(template["default_amount_cents"])
        occurrence = self.get_projection_occurrence(int(template["id"]), month)
        if occurrence is not None:
            template_amount = int(occurrence["amount_cents"])
        difference = abs(int(transaction["amount_cents"]) - template_amount)
        if difference == 0:
            score += 26
        elif difference <= 150:
            score += 10

        if transaction["is_fixed"]:
            score += 10
        return score

    @staticmethod
    def _normalize_text(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value.strip().lower())
        return "".join(ch for ch in normalized if not unicodedata.combining(ch))

    @staticmethod
    def _month_distance(start_month: str, end_month: str) -> int:
        start = datetime.fromisoformat(start_month + "-01")
        end = datetime.fromisoformat(end_month + "-01")
        return (end.year - start.year) * 12 + (end.month - start.month)

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
