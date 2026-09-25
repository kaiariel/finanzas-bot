from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import datetime
from difflib import get_close_matches
from pathlib import Path
from string import Template

from finance_bot.config import Settings
from finance_bot.db import FinanceDatabase
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
    VALID_CATEGORIES,
)


def _user_label(row, aliases: dict[int, str] | None = None) -> str:
    aliases = aliases or {}
    user_id = row["telegram_user_id"]
    if user_id in aliases:
        return aliases[user_id]
    return row["telegram_full_name"] or row["telegram_username"] or "Sin usuario"


def _file_url(path_value: str | None) -> str:
    if not path_value:
        return ""
    try:
        # abspath normaliza sin tocar el disco. resolve() consultaba cada archivo
        # (lento en la unidad de Google Drive) y se llevaba ~70% del tiempo del panel.
        return Path(os.path.abspath(path_value)).as_uri()
    except ValueError:
        return ""


def _normalize_text(value: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFKD", value) if not unicodedata.combining(char)
    ).replace("?", "").strip().lower()


_NORMALIZED_CATEGORY_MAP = {
    _normalize_text(category): category for category in VALID_CATEGORIES
}
_LEGACY_CATEGORY_MAP = {
    _normalize_text(category): HOUSEHOLD_FOOD_CATEGORY
    for category in LEGACY_HOUSEHOLD_FOOD_CATEGORIES
}


def _normalize_category(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    normalized = _normalize_text(raw)
    legacy = _LEGACY_CATEGORY_MAP.get(normalized)
    if legacy:
        return legacy
    exact = _NORMALIZED_CATEGORY_MAP.get(normalized)
    if exact:
        return exact

    close_match = get_close_matches(
        normalized,
        _NORMALIZED_CATEGORY_MAP.keys(),
        n=1,
        cutoff=0.75,
    )
    if close_match:
        return _NORMALIZED_CATEGORY_MAP[close_match[0]]
    return raw


def _transaction_payload(rows, aliases: dict[int, str]) -> list[dict[str, object]]:
    transactions: list[dict[str, object]] = []
    for row in rows:
        created_at = parse_created_at(row["created_at"])
        receipt = row["receipt_local_path"] or ""
        try:
            inference_notes = json.loads(row["inference_notes"] or "[]")
        except json.JSONDecodeError:
            inference_notes = []
        transactions.append(
            {
                "id": row["id"],
                "createdAt": row["created_at"],
                "monthKey": created_at.strftime("%Y-%m"),
                "monthName": format_month(created_at),
                "date": format_date(created_at),
                "dateIso": created_at.strftime("%Y-%m-%d"),
                "description": row["note"],
                "sourceText": row["source_text"] or "",
                "category": _normalize_category(row["category"]),
                "amount": format_euro(row["amount_cents"]),
                "amountCents": row["amount_cents"],
                "kind": row["kind"],
                "type": tipo_label(row["kind"]),
                "store": _store_label(row["store"] or ""),
                "isFixed": bool(row["is_fixed"]),
                "fixed": fixed_label(bool(row["is_fixed"])),
                "userId": row["telegram_user_id"],
                "user": _user_label(row, aliases),
                "receipt": receipt,
                "receiptUrl": _file_url(receipt),
                "hasReceipt": bool(receipt),
                "receiptId": row["receipt_id"],
                "attachmentType": _attachment_type(receipt),
                "reviewStatus": row["review_status"] or "registered",
                "duplicateOfId": row["duplicate_of_id"],
                "inferenceNotes": inference_notes,
                "projectionTemplateId": row["projection_template_id"],
                "accountId": row["account_id"] if "account_id" in row.keys() else None,
                "dueDate": row["due_date"] if "due_date" in row.keys() else None,
                "paidAmountCents": row["paid_amount_cents"] if "paid_amount_cents" in row.keys() else row["amount_cents"],
            }
        )
    return transactions


def _store_label(value: str) -> str:
    key = _normalize_text(value)
    return {"ale-hop": "ALE-HOP", "frutera vecino": "Frutería Vecino"}.get(key, value.strip())


def _attachment_type(value: str) -> str:
    suffix = Path(value).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return "imagen"
    if suffix in {".ogg", ".mp3", ".wav", ".m4a", ".opus"}:
        return "audio"
    return "documento" if value else ""


def _receipt_payload(rows, aliases: dict[int, str]) -> list[dict[str, object]]:
    receipts: list[dict[str, object]] = []
    for row in rows:
        created_at = parse_created_at(row["created_at"])
        path = row["local_path"]
        receipts.append(
            {
                "id": row["id"],
                "createdAt": row["created_at"],
                "monthKey": created_at.strftime("%Y-%m"),
                "date": format_date(created_at),
                "status": row["status"],
                "dateIso": created_at.strftime("%Y-%m-%d"),
                "path": path,
                "url": _file_url(path),
                "caption": row["caption"] or "",
                "reviewNotes": row["review_notes"] or "",
                "user": _user_label(row, aliases),
                "attachmentType": _attachment_type(path),
            }
        )
    return receipts


def _add_months(date_value: datetime, months: int) -> datetime:
    month_index = date_value.month - 1 + months
    year = date_value.year + month_index // 12
    month = month_index % 12 + 1
    return date_value.replace(year=year, month=month, day=1)


def _month_distance(start_month: str, end_month: str) -> int:
    start = datetime.fromisoformat(start_month + "-01")
    end = datetime.fromisoformat(end_month + "-01")
    return (end.year - start.year) * 12 + (end.month - start.month)


def _projection_status_label(kind: str, status: str) -> str:
    if status == "completed":
        return "Cobrado" if kind == "income" else "Pagado"
    if status == "skipped":
        return "Omitido"
    return "Pendiente"


def _projection_payload(
    db: FinanceDatabase, transactions: list[dict[str, object]], month_count: int = 36
) -> dict[str, object]:
    start = datetime.now(db.timezone).replace(day=1)
    months = [_add_months(start, index).strftime("%Y-%m") for index in range(month_count)]
    templates = db.list_projection_templates()
    overrides = {
        (row["template_id"], row["month"]): row
        for row in db.list_projection_occurrences(months[0], months[-1])
    }

    actual_by_month = {
        month: {"income": 0, "expense": 0}
        for month in months
    }
    actual_expense_by_month_category = {month: {} for month in months}
    for transaction in transactions:
        month = str(transaction["monthKey"])
        kind = str(transaction["kind"])
        if month in actual_by_month and kind in actual_by_month[month]:
            amount_cents = int(transaction["amountCents"])
            actual_by_month[month][kind] += amount_cents
            if kind == "expense":
                category = str(transaction["category"] or "")
                category_totals = actual_expense_by_month_category[month]
                category_totals[category] = category_totals.get(category, 0) + amount_cents

    rows: list[dict[str, object]] = []
    summaries = {
        month: {
            "monthKey": month,
            "monthName": format_month(datetime.fromisoformat(month + "-01")),
            "projectedIncomeCents": 0,
            "projectedExpenseCents": 0,
            "completedIncomeCents": 0,
            "completedExpenseCents": 0,
            "pendingIncomeCents": 0,
            "pendingExpenseCents": 0,
            "actualIncomeCents": actual_by_month[month]["income"],
            "actualExpenseCents": actual_by_month[month]["expense"],
        }
        for month in months
    }

    for month_index, month in enumerate(months):
        for template in templates:
            start_month = template["start_month"] or months[0]
            month_offset = _month_distance(start_month, month)
            if month_offset < 0:
                continue
            end_month = str(template["end_month"] or "")
            if end_month and month > end_month:
                continue

            installment_current = template["installment_current"]
            installment_total = template["installment_total"]
            if installment_current and installment_total:
                installment_for_month = int(installment_current) + month_offset
                if installment_for_month > int(installment_total):
                    continue
                remaining_installments = int(installment_total) - installment_for_month
                installment_label = f"{installment_for_month}/{installment_total}"
                remaining_label = f"Restan {remaining_installments}"
            else:
                installment_for_month = None
                remaining_installments = None
                installment_label = "Fijo"
                remaining_label = ""

            override = overrides.get((template["id"], month))
            amount_cents = (
                int(override["amount_cents"])
                if override is not None
                else int(template["default_amount_cents"])
            )
            status = str(override["status"]) if override is not None else "pending"
            note = str(override["note"]) if override is not None else ""
            kind = str(template["kind"])
            category = _normalize_category(template["category"] or "")
            template_name_key = _normalize_text(str(template["name"] or ""))
            tracks_actual_category = (
                kind == "expense"
                and category == HOUSEHOLD_FOOD_CATEGORY
                and template_name_key
                in {
                    _normalize_text("Hogar/Alimentacion"),
                    _normalize_text("Hogar/Alimentación"),
                    _normalize_text("Hogar y Alimentacion"),
                    _normalize_text(HOUSEHOLD_FOOD_CATEGORY),
                }
                and status != "skipped"
            )
            actual_spent_cents = 0
            remaining_budget_cents = amount_cents
            if tracks_actual_category:
                actual_spent_cents = actual_expense_by_month_category[month].get(category, 0)
                remaining_budget_cents = amount_cents - actual_spent_cents
            summary = summaries[month]
            linked = [t for t in transactions if t["projectionTemplateId"] == template["id"]
                      and t["monthKey"] == month]
            actual_linked = sum(int(t["amountCents"]) for t in linked if t["kind"] == kind)
            name_tokens = {token for token in re.findall(r"\w+", template_name_key) if len(token) >= 3}
            link_warnings = []
            for transaction in linked:
                if transaction.get("reviewStatus") == "reviewed":
                    continue
                mismatched = transaction["kind"] != kind or transaction["category"] != category
                # Un sobre por categoria (p. ej. Hogar y Alimentacion) recoge cualquier
                # compra de esa categoria: "Fresa platano" no tiene por que nombrarlo.
                if not tracks_actual_category and not mismatched:
                    tokens = set(re.findall(r"\w+", _normalize_text(str(transaction["description"]) + " " + str(transaction["store"]))))
                    mismatched = not name_tokens.intersection(tokens)
                if mismatched:
                    link_warnings.append("Revisar vínculo con movimiento #" + str(transaction["id"]))
            # En un sobre, gastar distinto de lo presupuestado es lo normal (ya se
            # muestra como "Gastado real / falta"), no una discrepancia a conciliar.
            if linked and actual_linked != amount_cents and not tracks_actual_category:
                link_warnings.append("El importe registrado difiere del previsto")
            if status == "completed" and not linked and not tracks_actual_category:
                link_warnings.append("Marcado manualmente; sin movimiento vinculado")
            if linked and status != "completed" and not tracks_actual_category:
                link_warnings.append("Hay movimientos vinculados, pero el concepto no está completado")

            if status != "skipped":
                key = "projectedIncomeCents" if kind == "income" else "projectedExpenseCents"
                summary[key] += amount_cents
            if tracks_actual_category:
                summary["completedExpenseCents"] += actual_spent_cents
                summary["pendingExpenseCents"] += max(0, remaining_budget_cents)
            elif status == "completed":
                key = "completedIncomeCents" if kind == "income" else "completedExpenseCents"
                summary[key] += actual_linked
            elif status == "pending":
                key = "pendingIncomeCents" if kind == "income" else "pendingExpenseCents"
                summary[key] += max(0, amount_cents - actual_linked)

            display_note = note
            if tracks_actual_category:
                budget_note = (
                    f"Gastado real: {format_euro(actual_spent_cents)} · "
                    f"falta: {format_euro(remaining_budget_cents)}"
                )
                display_note = f"{display_note} · {budget_note}" if display_note else budget_note

            rows.append(
                {
                    "templateId": template["id"],
                    "linkedTransactionIds": [t["id"] for t in linked],
                    "actualLinkedCents": actual_linked,
                    "linkWarnings": link_warnings,
                    "month": month,
                    "monthName": summary["monthName"],
                    "kind": kind,
                    "type": tipo_label(kind),
                    "name": template["name"],
                    "category": category,
                    "group": template["group_name"] or "",
                    "startMonth": start_month,
                    "endMonth": end_month,
                    "amountCents": amount_cents,
                    "amount": format_euro(amount_cents),
                    "defaultAmountCents": template["default_amount_cents"],
                    "status": status,
                    "statusLabel": "Variable" if tracks_actual_category else _projection_status_label(kind, status),
                    "note": display_note,
                    "storedNote": note,
                    "tracksActualCategory": tracks_actual_category,
                    "actualSpentCents": actual_spent_cents,
                    "actualSpent": format_euro(actual_spent_cents),
                    "remainingBudgetCents": remaining_budget_cents,
                    "remainingBudget": format_euro(remaining_budget_cents),
                    "installmentLabel": installment_label,
                    "remainingInstallments": remaining_installments,
                    "remainingLabel": remaining_label,
                    "installmentCurrent": installment_for_month,
                    "installmentTotal": installment_total,
                    "sortOrder": template["sort_order"],
                }
            )

    month_summaries: list[dict[str, object]] = []
    for summary in summaries.values():
        projected_balance = summary["projectedIncomeCents"] - summary["projectedExpenseCents"]
        actual_balance = summary["actualIncomeCents"] - summary["actualExpenseCents"]
        summary["projectedBalanceCents"] = projected_balance
        summary["actualBalanceCents"] = actual_balance
        summary["projectedIncome"] = format_euro(summary["projectedIncomeCents"])
        summary["projectedExpense"] = format_euro(summary["projectedExpenseCents"])
        summary["projectedBalance"] = format_euro(projected_balance)
        summary["completedIncome"] = format_euro(summary["completedIncomeCents"])
        summary["completedExpense"] = format_euro(summary["completedExpenseCents"])
        summary["pendingIncome"] = format_euro(summary["pendingIncomeCents"])
        summary["pendingExpense"] = format_euro(summary["pendingExpenseCents"])
        summary["actualIncome"] = format_euro(summary["actualIncomeCents"])
        summary["actualExpense"] = format_euro(summary["actualExpenseCents"])
        summary["actualBalance"] = format_euro(actual_balance)
        month_summaries.append(summary)

    return {"months": month_summaries, "rows": rows}


def report_data(settings: Settings, *, editable: bool = False) -> dict:
    from finance_bot.storage import inspect_database, read_backup_status
    health = inspect_database(settings.sqlite_db_path)
    settings.ensure_core_dirs()
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone)
    transactions = _transaction_payload(db.list_transactions(), settings.telegram_user_aliases)
    receipts = _receipt_payload(db.list_receipts(), settings.telegram_user_aliases)
    projections = _projection_payload(db, transactions)
    available = {}
    for row in transactions + receipts:
        path = str(row.get("receipt", row.get("path", "")) or "")
        if path not in available:
            try:
                available[path] = bool(path) and Path(path).is_file()
            except OSError:
                # OneDrive or a disconnected drive may deny the metadata check.
                # The row remains visible so the user can reconnect it later.
                available[path] = False
        row["fileAvailable"] = available[path]
        if editable:
            row["receiptUrl" if "receipt" in row else "url"] = (
                f"/api/attachments/{'transactions' if 'receipt' in row else 'receipts'}/{row['id']}" if path else ""
            )
    now = datetime.now(db.timezone)
    accounts = [{"id": row["id"], "name": row["name"], "type": row["account_type"], "balanceCents": row["balance_cents"]} for row in db.account_balances()]
    budgets = [{"id": row["id"], "month": row["month"], "category": row["category"], "amountCents": row["amount_cents"]} for row in db.list_budgets()]
    goals = [{"id": row["id"], "name": row["name"], "targetCents": row["target_cents"], "currentCents": row["current_cents"], "targetDate": row["target_date"], "walletId": row["wallet_id"]} for row in db.list_savings_goals()]
    savings_settings = db.get_savings_settings()
    savings_wallets = [{"id": row["id"], "name": row["name"], "balanceCents": row["balance_cents"], "includeInProjection": bool(row["include_in_projection"]), "active": bool(row["active"])} for row in db.list_savings_wallets(active_only=False)]
    savings = {
        "currentBalanceCents": savings_settings["current_balance_cents"],
        "currentBalanceDate": savings_settings["current_balance_date"],
        "includeCurrentBalance": bool(savings_settings["include_current_balance"]),
        "emergencyMonthlyCents": savings_settings["emergency_monthly_cents"],
        "wallets": savings_wallets,
    }
    return {"transactions": transactions, "receipts": receipts, "projections": projections,
            "dataHealth": {"lastRecordAt": health["lastRecordAt"], "lastBackupAt": read_backup_status(settings.data_dir).get("verifiedAt")},
            "accounts": accounts, "budgets": budgets, "savingsGoals": goals, "savings": savings,
            "generatedAt": now.strftime("%d/%m/%Y %H:%M"), "today": now.strftime("%Y-%m-%d"),
            "editable": editable, "categories": list(VALID_CATEGORIES)}


def render_report_html(settings: Settings, *, editable: bool = False) -> str:
    data = report_data(settings, editable=editable)
    assets = Path(__file__).with_name("ui")
    return Template((assets / "dashboard.html").read_text(encoding="utf-8")).substitute(
        css=(assets / "dashboard.css").read_text(encoding="utf-8"),
        js=(assets / "dashboard.js").read_text(encoding="utf-8"),
        data=json.dumps(data, ensure_ascii=False).replace("<", "\\u003c"),
        runtime=('<div id="runtimeStatus" class="runtime" role="status"><span id="runtimeText">Comprobando conexión…</span>'
                 '<button id="runtimeRefresh" class="quiet">Actualizar estado</button></div>') if editable else '',
    )


def generate_report(settings: Settings, output_path: Path | None = None) -> Path:
    output = output_path or settings.report_html_path
    html = render_report_html(settings, editable=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    return output
