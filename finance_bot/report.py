from __future__ import annotations

import json
import unicodedata
from datetime import datetime
from difflib import get_close_matches
from html import escape
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
    path = Path(path_value)
    if not path.is_absolute():
        path = Path.cwd() / path
    try:
        return path.resolve().as_uri()
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
                "store": row["store"] or "",
                "isFixed": bool(row["is_fixed"]),
                "fixed": fixed_label(bool(row["is_fixed"])),
                "userId": row["telegram_user_id"],
                "user": _user_label(row, aliases),
                "receipt": receipt,
                "receiptUrl": _file_url(receipt),
                "hasReceipt": bool(receipt),
                "reviewStatus": row["review_status"] or "registered",
                "duplicateOfId": row["duplicate_of_id"],
                "inferenceNotes": inference_notes,
                "projectionTemplateId": row["projection_template_id"],
            }
        )
    return transactions


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
                "path": path,
                "url": _file_url(path),
                "caption": row["caption"] or "",
                "reviewNotes": row["review_notes"] or "",
                "user": _user_label(row, aliases),
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
    db: FinanceDatabase, transactions: list[dict[str, object]], month_count: int = 12
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

            if status != "skipped":
                key = "projectedIncomeCents" if kind == "income" else "projectedExpenseCents"
                summary[key] += amount_cents
            if tracks_actual_category:
                summary["completedExpenseCents"] += actual_spent_cents
                summary["pendingExpenseCents"] += remaining_budget_cents
            elif status == "completed":
                key = "completedIncomeCents" if kind == "income" else "completedExpenseCents"
                summary[key] += amount_cents
            elif status == "pending":
                key = "pendingIncomeCents" if kind == "income" else "pendingExpenseCents"
                summary[key] += amount_cents

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
                    "month": month,
                    "monthName": summary["monthName"],
                    "kind": kind,
                    "type": tipo_label(kind),
                    "name": template["name"],
                    "category": category,
                    "group": template["group_name"] or "",
                    "startMonth": start_month,
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


def render_report_html(settings: Settings, *, editable: bool = False) -> str:
    settings.ensure_core_dirs()
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone)
    transactions = _transaction_payload(db.list_transactions(), settings.telegram_user_aliases)
    receipts = _receipt_payload(db.list_receipts(), settings.telegram_user_aliases)
    projections = _projection_payload(db, transactions)
    generated_at = datetime.now().strftime("%d/%m/%Y %H:%M")

    return _render_html(
        transactions=transactions,
        receipts=receipts,
        projections=projections,
        generated_at=generated_at,
        db_path=str(settings.sqlite_db_path),
        editable=editable,
        valid_categories=list(VALID_CATEGORIES),
    )


def generate_report(settings: Settings, output_path: Path | None = None) -> Path:
    output = output_path or settings.report_html_path
    html = render_report_html(settings, editable=False)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html, encoding="utf-8")
    return output


def _render_html(
    *,
    transactions: list[dict[str, object]],
    receipts: list[dict[str, object]],
    projections: dict[str, object],
    generated_at: str,
    db_path: str,
    editable: bool,
    valid_categories: list[str],
) -> str:
    template = Template(
        """<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  $refresh_meta
  <title>$title</title>
  <style>
    :root {
      --bg: #f5f7f4;
      --panel: #ffffff;
      --ink: #17211d;
      --muted: #60706a;
      --line: #dce4dd;
      --accent: #1f7a5a;
      --accent-2: #2c6387;
      --warn: #936719;
      --danger: #b34545;
      --soft: #edf3ee;
      --radius: 8px;
    }
    * { box-sizing: border-box; }
    [hidden] { display: none !important; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      font-size: 15px;
    }
    header {
      border-bottom: 1px solid var(--line);
      background: var(--soft);
      padding: 18px clamp(16px, 4vw, 40px);
    }
    h1 { margin: 0; font-size: 24px; line-height: 1.2; }
    header p { margin: 6px 0 0; color: var(--muted); }
    main {
      width: min(1440px, 100%);
      margin: 0 auto;
      padding: 18px clamp(12px, 3vw, 32px) 40px;
    }
    .filters {
      display: grid;
      grid-template-columns: repeat(7, minmax(120px, 1fr));
      gap: 10px;
      margin-bottom: 14px;
      align-items: end;
    }
    label {
      display: grid;
      gap: 5px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
    }
    select, input {
      width: 100%;
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--panel);
      color: var(--ink);
      padding: 8px 10px;
      font: inherit;
    }
    button {
      min-height: 38px;
      border: 1px solid var(--accent);
      border-radius: var(--radius);
      background: var(--accent);
      color: white;
      font-weight: 700;
      cursor: pointer;
    }
    button.secondary {
      border-color: var(--line);
      background: var(--panel);
      color: var(--ink);
    }
    button.linkish {
      min-height: 30px;
      padding: 4px 9px;
      border-color: var(--line);
      background: #fbfcfb;
      color: var(--accent-2);
      font-size: 12px;
    }
    .tabs {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 14px;
    }
    .tab-button {
      border-color: var(--line);
      background: var(--panel);
      color: var(--ink);
    }
    .tab-button.active {
      border-color: var(--accent);
      background: var(--accent);
      color: white;
    }
    .cards {
      display: grid;
      grid-template-columns: repeat(7, minmax(130px, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }
    .projection-tools {
      display: grid;
      grid-template-columns: minmax(180px, 260px) 1fr;
      gap: 12px;
      align-items: end;
      padding: 12px;
      border-bottom: 1px solid var(--line);
    }
    .tab-panel {
      display: grid;
      gap: 14px;
      margin-bottom: 14px;
    }
    .summary-grid {
      display: grid;
      grid-template-columns: repeat(6, minmax(130px, 1fr));
      gap: 10px;
      padding: 12px;
    }
    .projection-overview { grid-template-columns: repeat(5, minmax(150px, 1fr)); }
    .analytics-summary { grid-template-columns: repeat(6, minmax(130px, 1fr)); }
    .cashflow-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(260px, 1fr));
      gap: 10px;
    }
    .cashflow-list {
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--panel);
      min-height: 120px;
      overflow: hidden;
    }
    .cashflow-list h3 {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 10px;
      margin: 0;
      padding: 10px;
      border-bottom: 1px solid var(--line);
      font-size: 13px;
    }
    .cashflow-list h3 strong { white-space: nowrap; }
    .cashflow-list .items {
      display: grid;
      gap: 1px;
      max-height: 260px;
      overflow: auto;
    }
    .cashflow-item {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px;
      padding: 8px 10px;
      border-bottom: 1px solid var(--line);
      background: white;
    }
    .cashflow-group {
      border-bottom: 1px solid var(--line);
      background: var(--panel-soft);
    }
    .cashflow-group:last-child {
      border-bottom: 0;
    }
    .cashflow-group-header {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px;
      padding: 9px 10px;
      border-bottom: 1px solid var(--line);
      font-size: 12px;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      background: color-mix(in srgb, var(--panel) 82%, white);
    }
    .cashflow-group-items .cashflow-item:last-child {
      border-bottom: 0;
    }
    .cashflow-item span:first-child {
      overflow-wrap: anywhere;
    }
    .section-heading {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: baseline;
      padding: 14px 12px 8px;
    }
    .section-heading h2 { margin: 0; padding: 0; border: 0; }
    .section-heading p { margin: 0; }
    .analytics-tools {
      display: grid;
      grid-template-columns: minmax(180px, 260px) 1fr;
      gap: 12px;
      align-items: end;
      padding: 12px;
      border-bottom: 1px solid var(--line);
    }
    .analytics-grid {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
      gap: 14px;
      margin-top: 14px;
    }
    .analytics-panel[hidden] { display: none; }
    .recommendation-title {
      display: block;
      margin-bottom: 4px;
      font-weight: 800;
    }
    .score-good { color: var(--accent); }
    .score-warn { color: var(--warn); }
    .score-danger { color: var(--danger); }
    .card, .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
    }
    .card { padding: 12px; }
    .card span {
      display: block;
      color: var(--muted);
      font-size: 11px;
      font-weight: 800;
      text-transform: uppercase;
    }
    .card strong {
      display: block;
      margin-top: 5px;
      font-size: 21px;
    }
    .grid {
      display: grid;
      grid-template-columns: minmax(0, 1.2fr) minmax(320px, .8fr);
      gap: 14px;
    }
    .panel { overflow: hidden; }
    .panel h2 {
      margin: 0;
      padding: 13px 14px;
      border-bottom: 1px solid var(--line);
      font-size: 16px;
    }
    .chart-wrap { height: 276px; padding: 12px; }
    canvas { width: 100%; height: 100%; display: block; }
    .list {
      display: grid;
      gap: 8px;
      padding: 12px;
      max-height: 275px;
      overflow: auto;
    }
    .notice, .receipt-item {
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: #fbfcfb;
      padding: 10px;
      overflow-wrap: anywhere;
    }
    .notice.warn { border-color: #e3c47e; background: #fff8e9; }
    .notice.danger { border-color: #e2a6a6; background: #fff2f2; }
    .table-wrap { max-height: 520px; overflow: auto; }
    table { width: 100%; border-collapse: collapse; font-size: 14px; }
    th, td {
      padding: 9px 10px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      white-space: nowrap;
    }
    td.description { min-width: 220px; white-space: normal; }
    th {
      position: sticky;
      top: 0;
      background: #f8faf7;
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      z-index: 1;
    }
    thead .filter-row th {
      top: 36px;
      background: #fbfcfb;
      padding-top: 6px;
      padding-bottom: 6px;
      text-transform: none;
      font-size: 11px;
    }
    .filter-row select {
      min-height: 30px;
      padding: 4px 8px;
      font-size: 12px;
    }
    .amount { text-align: right; font-variant-numeric: tabular-nums; }
    .income { color: var(--accent); font-weight: 800; }
    .expense { color: var(--danger); font-weight: 800; }
    .muted { color: var(--muted); }
    .pill {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 2px 8px;
      border-radius: 999px;
      background: #eef4f1;
      color: var(--muted);
      font-size: 12px;
      font-weight: 800;
    }
    .status-completed { color: var(--accent); font-weight: 800; }
    .status-pending { color: var(--warn); font-weight: 800; }
    .status-skipped { color: var(--muted); font-weight: 800; }
    .status-chip {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      min-height: 24px;
      padding: 2px 10px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 800;
      white-space: nowrap;
    }
    .status-chip.status-completed { background: #e3f1ea; }
    .status-chip.status-pending { background: #fbf0d8; }
    .status-chip.status-skipped { background: #eaeeec; }
    .quick-status {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      min-height: 28px;
      padding: 2px 10px;
      border: 1px solid var(--accent);
      border-radius: 8px;
      background: #fff;
      color: var(--accent);
      font: inherit;
      font-size: 12px;
      font-weight: 800;
      cursor: pointer;
      white-space: nowrap;
    }
    .quick-status:hover { background: #e3f1ea; }
    .quick-status.undo { border-color: var(--line); color: var(--muted); }
    .quick-status.undo:hover { background: #eaeeec; }
    .quick-status:disabled { opacity: .5; cursor: wait; }
    .cashflow-actions { display: inline-flex; align-items: center; gap: 8px; justify-self: end; }
    .legend-proj-income { background: #1f7a5a; }
    .legend-proj-expense { background: #b34545; }
    .legend-proj-balance { background: #2c6387; }
    .legend-actual-balance { background: #17211d; }
    .modal-backdrop {
      position: fixed;
      inset: 0;
      z-index: 10;
      display: grid;
      place-items: center;
      padding: 18px;
      background: rgba(23, 33, 29, .42);
    }
    .modal-backdrop[hidden] { display: none; }
    .modal {
      width: min(680px, 100%);
      max-height: min(720px, 92vh);
      overflow: auto;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: 0 20px 60px rgba(0, 0, 0, .18);
    }
    .modal header {
      background: #f8faf7;
      padding: 14px 16px;
    }
    .modal h2 { margin: 0; font-size: 18px; }
    .modal form {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
      padding: 16px;
    }
    .modal label.full { grid-column: 1 / -1; }
    .modal .actions {
      grid-column: 1 / -1;
      display: flex;
      justify-content: flex-end;
      gap: 10px;
      border-top: 1px solid var(--line);
      padding-top: 12px;
    }
    .form-error {
      grid-column: 1 / -1;
      color: var(--danger);
      font-weight: 700;
    }
    a { color: var(--accent-2); font-weight: 700; text-decoration: none; }
    a:hover { text-decoration: underline; }
    @media (max-width: 1100px) {
      .filters, .cards, .projection-tools, .summary-grid, .cashflow-grid, .analytics-tools, .analytics-grid, .grid { grid-template-columns: 1fr; }
      .chart-wrap { height: 240px; }
      .modal form { grid-template-columns: 1fr; }
    }

    /* ---- Rediseño: jerarquía, estados y claridad ---- */
    .mode-badge {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      margin-top: 8px;
      padding: 3px 10px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: .02em;
    }
    .mode-badge.read { background: #e7eef4; color: var(--accent-2); }
    .mode-badge.edit { background: #e3f1ea; color: var(--accent); }
    .mode-hint { margin: 6px 0 0; color: var(--muted); font-size: 13px; }
    .mode-actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 10px; }
    .mode-actions a {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 38px;
      padding: 0 14px;
      border-radius: 10px;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--accent-2);
      font-size: 14px;
      font-weight: 800;
      text-decoration: none;
      box-shadow: 0 4px 10px rgba(0, 0, 0, .04);
    }
    .mode-actions a.primary {
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
    }
    .mode-actions a:hover { text-decoration: none; filter: brightness(.98); }
    .runtime-status {
      width: min(1440px, calc(100% - 24px));
      margin: 14px auto 0;
      padding: 12px 14px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--panel);
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 10px 18px;
      box-shadow: 0 8px 24px rgba(23, 33, 29, .05);
    }
    .runtime-status strong { margin-right: auto; }
    .runtime-item { display: inline-flex; align-items: center; gap: 7px; color: var(--muted); font-size: 13px; }
    .runtime-dot { width: 9px; height: 9px; border-radius: 999px; background: var(--muted); }
    .runtime-dot.online { background: var(--accent); box-shadow: 0 0 0 3px rgba(31, 122, 90, .12); }
    .runtime-dot.offline { background: var(--danger); box-shadow: 0 0 0 3px rgba(179, 69, 69, .12); }
    .runtime-refresh { min-height: 30px; padding: 4px 10px; font-size: 12px; }

    /* Resumen con jerarquía: balance protagonista */
    .summary {
      display: grid;
      grid-template-columns: minmax(220px, 1.4fr) repeat(3, minmax(120px, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }
    .summary .secondary-cards {
      grid-column: 2 / -1;
      display: grid;
      grid-template-columns: repeat(3, minmax(110px, 1fr));
      grid-template-rows: 1fr 1fr;
      gap: 10px;
    }
    .card.hero {
      grid-row: 1;
      display: flex;
      flex-direction: column;
      justify-content: center;
      gap: 4px;
      padding: 16px 18px;
      border-width: 2px;
    }
    .card.hero span { font-size: 12px; }
    .card.hero strong { font-size: 34px; line-height: 1.05; }
    .card.hero.balance-pos { border-color: var(--accent); background: #f1f8f4; }
    .card.hero.balance-pos strong { color: var(--accent); }
    .card.hero.balance-neg { border-color: var(--danger); background: #fdf3f3; }
    .card.hero.balance-neg strong { color: var(--danger); }
    .card .delta {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      margin-top: 2px;
      font-size: 12px;
      font-weight: 700;
      text-transform: none;
      letter-spacing: 0;
    }
    .delta.up { color: var(--accent); }
    .delta.down { color: var(--danger); }
    .delta.flat { color: var(--muted); }

    /* Filtros: primarios visibles, resto plegable */
    .filters-bar {
      display: grid;
      grid-template-columns: minmax(150px, 220px) minmax(180px, 1fr) auto auto;
      gap: 10px;
      align-items: end;
      margin-bottom: 12px;
    }
    .filters-more {
      display: grid;
      grid-template-columns: repeat(5, minmax(120px, 1fr));
      gap: 10px;
      margin-bottom: 14px;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: var(--radius);
      background: var(--panel);
    }
    .filters-more[hidden] { display: none; }

    /* Leyenda de gráficas */
    .chart-legend {
      display: flex;
      flex-wrap: wrap;
      gap: 14px;
      padding: 0 12px 8px;
      font-size: 12px;
      color: var(--muted);
      font-weight: 700;
    }
    .chart-legend span { display: inline-flex; align-items: center; gap: 6px; }
    .legend-dot { width: 11px; height: 11px; border-radius: 3px; display: inline-block; }
    .legend-income { background: var(--accent); }
    .legend-expense { background: var(--danger); }

    /* Estados legibles en bandeja de tickets */
    .status-badge {
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      padding: 2px 9px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: .02em;
    }
    .status-badge.tone-ok { background: #e3f1ea; color: var(--accent); }
    .status-badge.tone-warn { background: #fbf0d8; color: var(--warn); }
    .status-badge.tone-danger { background: #f8dede; color: var(--danger); }
    .status-badge.tone-neutral { background: #eaeeec; color: var(--muted); }
    .receipt-head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
    .receipt-path { margin-top: 6px; font-size: 12px; }
    .receipt-path summary { cursor: pointer; color: var(--accent-2); font-weight: 700; }
    .receipt-path code { color: var(--muted); word-break: break-all; font-size: 11px; }
    .section-heading {
      display: flex;
      flex-wrap: wrap;
      align-items: baseline;
      justify-content: space-between;
      gap: 8px;
      padding: 13px 14px;
      border-bottom: 1px solid var(--line);
    }
    .section-heading h2 {
      padding: 0;
      border: 0;
    }
    .section-totals {
      color: var(--muted);
      font-size: 13px;
      font-weight: 700;
    }

    /* Bloques de alertas vs info */
    .insight-block + .insight-block { margin-top: 12px; }
    .insight-block h3 {
      margin: 0 0 8px;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: .03em;
      color: var(--muted);
    }

    @media (max-width: 1100px) {
      .summary, .summary .secondary-cards, .filters-bar, .filters-more { grid-template-columns: 1fr; }
      .summary .secondary-cards { grid-column: 1; grid-template-rows: none; }
      .card.hero strong { font-size: 30px; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Reporte de finanzas</h1>
    <p class="mode-hint">Generado el $generated_at</p>
    $edit_hint
  </header>
  $runtime_status
  <main>
    <nav class="tabs" aria-label="Pestañas del panel">
      <button class="tab-button active" type="button" data-tab="dashboard">Panel inicial</button>
      <button class="tab-button" type="button" data-tab="projection">Proyección</button>
      <button class="tab-button" type="button" data-tab="analytics">Diagnóstico</button>
    </nav>

    <section class="filters-bar dashboard-panel" aria-label="Filtros principales">
      <label>Mes<select id="monthFilter"></select></label>
      <label>Buscar<input id="searchFilter" type="search" placeholder="Descripción o tienda"></label>
      <button id="toggleFilters" class="secondary" type="button" aria-expanded="false">Más filtros</button>
      <button id="resetFilters" class="secondary" type="button">Limpiar</button>
    </section>

    <section class="filters-more dashboard-panel" id="moreFilters" aria-label="Filtros avanzados" hidden>
      <label>Categoría<select id="categoryFilter"></select></label>
      <label>Tipo<select id="typeFilter"></select></label>
      <label>Usuario<select id="userFilter"></select></label>
      <label>Fijo<select id="fixedFilter"></select></label>
      <label>Ticket<select id="receiptFilter"></select></label>
    </section>

    <section class="summary dashboard-panel" aria-label="Resumen del periodo">
      <div class="card hero" id="balanceCard">
        <span>Balance</span>
        <strong id="balanceTotal">0,00 €</strong>
        <span class="delta" id="balanceMeta"></span>
      </div>
      <div class="secondary-cards">
        <div class="card"><span>Ingresos</span><strong id="incomeTotal">0,00 €</strong><span class="delta" id="incomeDelta"></span></div>
        <div class="card"><span>Egresos</span><strong id="expenseTotal">0,00 €</strong><span class="delta" id="expenseDelta"></span></div>
        <div class="card"><span>Con ticket</span><strong id="ticketCoverage">0%</strong></div>
        <div class="card"><span>Fijos</span><strong id="fixedTotal">0,00 €</strong></div>
        <div class="card"><span>Variables</span><strong id="variableTotal">0,00 €</strong></div>
        <div class="card"><span>Pendientes</span><strong id="pendingCount">0</strong></div>
      </div>
    </section>

    <section id="projectionPanel" class="tab-panel" hidden>
      <section class="panel">
        <h2>Proyección próximos meses</h2>
        <div class="projection-tools">
          <label>Mes proyectado<select id="projectionMonth"></select></label>
          <button id="addProjection" type="button">Añadir item</button>
          <div class="muted">Edita importes por mes y marca lo ya pagado o cobrado.</div>
        </div>
        <div class="summary-grid projection-overview" aria-label="Resumen proyectado">
          <div class="card"><span>Ingresos proyectados</span><strong id="projectionIncome">0,00 €</strong></div>
          <div class="card"><span>Gastos proyectados</span><strong id="projectionExpense">0,00 €</strong></div>
          <div class="card"><span>Balance proyectado</span><strong id="projectionBalance">0,00 €</strong></div>
          <div class="card"><span>Balance restante</span><strong id="projectionRemainingBalance">0,00 €</strong></div>
          <div class="card"><span>Balance real registrado</span><strong id="projectionActualBalance">0,00 €</strong></div>
        </div>
      </section>

      <section class="panel">
        <div class="section-heading">
          <h2>Tendencia 12 meses</h2>
          <div class="muted">Proyectado por mes y balance real registrado</div>
        </div>
        <div class="chart-legend">
          <span><i class="legend-dot legend-proj-income"></i>Ingresos proyectados</span>
          <span><i class="legend-dot legend-proj-expense"></i>Gastos proyectados</span>
          <span><i class="legend-dot legend-proj-balance"></i>Balance proyectado</span>
          <span><i class="legend-dot legend-actual-balance"></i>Balance real</span>
        </div>
        <div class="chart-wrap"><canvas id="trendChart"></canvas></div>
      </section>

      <section id="cashflowGrid" class="cashflow-grid" aria-label="Flujo restante del mes">
        <div class="cashflow-list">
          <h3><span>Ya cobrado</span><strong id="collectedIncomeTotal" class="income">0,00 €</strong></h3>
          <div id="collectedIncomeList" class="items"></div>
        </div>
        <div class="cashflow-list">
          <h3><span>Falta cobrar</span><strong id="pendingIncomeTotal" class="income">0,00 €</strong></h3>
          <div id="pendingIncomeList" class="items"></div>
        </div>
        <div class="cashflow-list">
          <h3><span>Ya pagado</span><strong id="paidExpenseTotal" class="expense">0,00 €</strong></h3>
          <div id="paidExpenseList" class="items"></div>
        </div>
        <div class="cashflow-list">
          <h3><span>Falta pagar</span><strong id="pendingExpenseTotal" class="expense">0,00 €</strong></h3>
          <div id="pendingExpenseList" class="items"></div>
        </div>
      </section>

      <section class="panel">
        <div class="section-heading">
          <h2>Detalle editable del mes</h2>
          <p class="muted">Añade, edita, borra o marca como pagado/cobrado.</p>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Concepto</th><th>Tipo</th><th>Categoría</th><th>Cuota</th><th>Restantes</th>
                <th class="amount">Mensual</th><th>Estado</th><th>Nota</th><th>Acciones</th>
              </tr>
            </thead>
            <tbody id="projectionBody"></tbody>
          </table>
        </div>
      </section>
    </section>

    <section id="analyticsPanel" class="analytics-panel" hidden>
      <section class="panel">
        <h2>Diagnóstico</h2>
        <div class="analytics-tools">
          <label>Mes analizado<select id="analyticsMonth"></select></label>
          <div class="muted">Lectura local de movimientos, tickets y proyecciones. No sustituye asesoría financiera.</div>
        </div>
        <div class="summary-grid analytics-summary" aria-label="Resumen del diagnóstico">
          <div class="card"><span>Score del mes</span><strong id="analyticsScore">0/100</strong></div>
          <div class="card"><span>Balance proyectado</span><strong id="analyticsProjectedBalance">0,00 €</strong></div>
          <div class="card"><span>Margen libre</span><strong id="analyticsSafetyMargin">0%</strong></div>
          <div class="card"><span>Fijos / ingresos</span><strong id="analyticsFixedRatio">0%</strong></div>
          <div class="card"><span>Mayor gasto real</span><strong id="analyticsTopExpense">Sin datos</strong></div>
          <div class="card"><span>Meses en riesgo</span><strong id="analyticsRiskMonths">0</strong></div>
        </div>
      </section>

      <section class="analytics-grid">
        <div class="panel">
          <h2>Diagnóstico del mes</h2>
          <div id="analyticsNarrative" class="list"></div>
        </div>
        <div class="panel">
          <h2>Recomendaciones</h2>
          <div id="analyticsRecommendations" class="list"></div>
        </div>
      </section>

      <section class="panel" style="margin-top: 14px;">
        <div class="section-heading">
          <h2>Avisos de IA</h2>
          <div id="analyticsAiAlertsCount" class="section-totals">Sin avisos</div>
        </div>
        <div id="analyticsAiAlerts" class="list"></div>
      </section>

      <section class="panel" style="margin-top: 14px;">
        <h2>Lectura de los próximos meses</h2>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Mes</th><th class="amount">Ingresos</th><th class="amount">Gastos</th>
                <th class="amount">Balance</th><th>Lectura</th>
              </tr>
            </thead>
            <tbody id="analyticsFutureBody"></tbody>
          </table>
        </div>
      </section>
    </section>

    <section class="grid dashboard-panel">
      <div class="panel">
        <h2>Alertas y salud</h2>
        <div class="list">
          <div class="insight-block">
            <h3>Necesita tu atención</h3>
            <div id="alertsList"></div>
          </div>
          <div class="insight-block">
            <h3>Para tu información</h3>
            <div id="infoList"></div>
          </div>
        </div>
      </div>
      <div class="panel">
        <div class="section-heading">
          <h2>Ingresos filtrados</h2>
          <div id="incomeListTotal" class="section-totals">0,00 €</div>
        </div>
        <div id="incomeList" class="list"></div>
      </div>
    </section>

    <section class="grid dashboard-panel" style="margin-top: 14px;">
      <div class="panel">
        <div class="section-heading">
          <h2>Gasto por dia</h2>
          <div id="dailyExpenseMeta" class="section-totals">Sin datos</div>
        </div>
        <div class="chart-legend">
          <span><i class="legend-dot legend-expense"></i>Gastos</span>
        </div>
        <div class="chart-wrap"><canvas id="monthlyChart"></canvas></div>
      </div>
      <div class="panel">
        <h2>Gasto por categoría</h2>
        <div class="chart-wrap"><canvas id="categoryChart"></canvas></div>
      </div>
    </section>

    <section class="panel dashboard-panel" style="margin-top: 14px;">
      <div class="section-heading">
        <h2>Movimientos</h2>
        <div id="transactionsTotals" class="section-totals">0 movimientos</div>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr id="transactionsHead">
              <th>Fecha</th><th>Descripción</th><th>Categoría</th>
              <th class="amount">Cantidad</th><th>Tipo</th><th>Tienda</th><th>Usuario</th><th>Fijo</th><th>Ticket</th>
            </tr>
            <tr class="filter-row">
              <th></th>
              <th></th>
              <th><select id="tableCategoryFilter"></select></th>
              <th><select id="amountSort"><option value="date_desc">Más reciente</option><option value="amount_asc">Cantidad asc</option><option value="amount_desc">Cantidad desc</option></select></th>
              <th><select id="tableTypeFilter"></select></th>
              <th><select id="tableStoreFilter"></select></th>
              <th><select id="tableUserFilter"></select></th>
              <th></th>
              <th></th>
            </tr>
          </thead>
          <tbody id="transactionsBody"></tbody>
        </table>
      </div>
    </section>

    <section class="panel dashboard-panel" style="margin-top: 14px;">
      <h2>Bandeja de tickets y audios</h2>
      <div id="receiptList" class="list"></div>
    </section>
  </main>

  <div id="editModal" class="modal-backdrop" hidden>
    <section class="modal" role="dialog" aria-modal="true" aria-labelledby="editTitle">
      <header>
        <h2 id="editTitle">Editar movimiento</h2>
      </header>
      <form id="editForm">
        <input id="editId" type="hidden">
        <label>Fecha<input id="editDate" name="date" type="date" required></label>
        <label>Cantidad<input id="editAmount" name="amount" type="text" inputmode="decimal" required></label>
        <label class="full">Descripción<input id="editDescription" name="description" type="text" required></label>
        <label>Categoría<select id="editCategory" name="category" required></select></label>
        <label>Tipo<select id="editKind" name="kind" required><option value="expense">Egreso</option><option value="income">Ingreso</option></select></label>
        <label>Tienda<input id="editStore" name="store" type="text"></label>
        <label>Es fijo<select id="editFixed" name="isFixed"><option value="false">No</option><option value="true">Sí</option></select></label>
        <div id="editError" class="form-error" hidden></div>
        <div class="actions">
          <button id="cancelEdit" class="secondary" type="button">Cancelar</button>
          <button type="submit">Guardar cambios</button>
        </div>
      </form>
    </section>
  </div>

  <div id="projectionModal" class="modal-backdrop" hidden>
    <section class="modal" role="dialog" aria-modal="true" aria-labelledby="projectionEditTitle">
      <header>
        <h2 id="projectionEditTitle">Editar proyeccion</h2>
      </header>
      <form id="projectionForm">
        <input id="projectionTemplateId" type="hidden">
        <input id="projectionEditMonth" type="hidden">
        <label class="full">Concepto<input id="projectionName" name="name" type="text" required></label>
        <label>Tipo<select id="projectionKind" name="kind" required><option value="expense">Egreso</option><option value="income">Ingreso</option></select></label>
        <label>Categoria<select id="projectionCategory" name="category" required></select></label>
        <label>Cantidad mensual<input id="projectionAmount" name="amount" type="text" inputmode="decimal" required></label>
        <label>Estado<select id="projectionStatus" name="status" required><option value="pending">Pendiente</option><option value="completed">Pagado/Cobrado</option><option value="skipped">Omitido</option></select></label>
        <label>Duracion<select id="projectionDuration" name="duration" required><option value="monthly">Fijo mensual</option><option value="once">Solo este mes</option><option value="installments">Cuotas</option></select></label>
        <label>Cuota actual<input id="projectionInstallmentCurrent" name="installmentCurrent" type="number" min="1" step="1"></label>
        <label>Total cuotas<input id="projectionInstallmentTotal" name="installmentTotal" type="number" min="1" step="1"></label>
        <label>Actualizar base<select id="projectionUpdateDefault" name="updateDefault"><option value="false">Solo este mes</option><option value="true">Tambien proximos meses</option></select></label>
        <label class="full">Nota<input id="projectionNote" name="note" type="text"></label>
        <div id="projectionError" class="form-error" hidden></div>
        <div class="actions">
          <button id="cancelProjectionEdit" class="secondary" type="button">Cancelar</button>
          <button type="submit">Guardar proyeccion</button>
        </div>
      </form>
    </section>
  </div>

  <script>
    const transactions = $transactions_json;
    const receipts = $receipts_json;
    const projections = $projections_json;
    const editable = $editable_json;
    const validCategories = $valid_categories_json;
    const money = new Intl.NumberFormat('es-ES', { style: 'currency', currency: 'EUR' });
    const filters = {
      month: document.getElementById('monthFilter'),
      category: document.getElementById('categoryFilter'),
      type: document.getElementById('typeFilter'),
      user: document.getElementById('userFilter'),
      fixed: document.getElementById('fixedFilter'),
      receipt: document.getElementById('receiptFilter'),
      search: document.getElementById('searchFilter')
    };
    const movementFilters = {
      category: document.getElementById('tableCategoryFilter'),
      type: document.getElementById('tableTypeFilter'),
      store: document.getElementById('tableStoreFilter'),
      user: document.getElementById('tableUserFilter'),
      amountSort: document.getElementById('amountSort')
    };
    const projectionMonth = document.getElementById('projectionMonth');
    const analyticsMonth = document.getElementById('analyticsMonth');

    function unique(values) {
      return Array.from(new Set(values.filter(Boolean))).sort(function(a, b) {
        return String(a).localeCompare(String(b), 'es');
      });
    }
    function normalizeText(value) {
      return String(value || '')
        .normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '')
        .toLowerCase()
        .trim();
    }
    function fillSelect(select, values, allLabel) {
      select.innerHTML = '';
      const all = document.createElement('option');
      all.value = '';
      all.textContent = allLabel;
      select.appendChild(all);
      values.forEach(function(value) {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = value;
        select.appendChild(option);
      });
    }
    function initFilters() {
      fillSelect(filters.month, unique(transactions.map(function(row) { return row.monthKey; })), 'Todos');
      fillSelect(filters.category, unique(transactions.map(function(row) { return row.category; })), 'Todas');
      fillSelect(filters.type, unique(transactions.map(function(row) { return row.type; })), 'Todos');
      fillSelect(filters.user, unique(transactions.map(function(row) { return row.user; })), 'Todos');
      fillSelect(filters.fixed, ['Sí', 'No'], 'Todos');
      fillSelect(filters.receipt, ['Con ticket', 'Sin ticket'], 'Todos');
      fillSelect(movementFilters.category, unique(transactions.map(function(row) { return row.category; })), 'Todas');
      fillSelect(movementFilters.type, unique(transactions.map(function(row) { return row.type; })), 'Todos');
      fillSelect(movementFilters.store, unique(transactions.map(function(row) { return row.store; })), 'Todas');
      fillSelect(movementFilters.user, unique(transactions.map(function(row) { return row.user; })), 'Todos');
    }
    function initEditForm() {
      const category = document.getElementById('editCategory');
      category.innerHTML = '';
      validCategories.forEach(function(value) {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = value;
        category.appendChild(option);
      });
    }
    function initProjectionForm() {
      const category = document.getElementById('projectionCategory');
      category.innerHTML = '';
      validCategories.forEach(function(value) {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = value;
        category.appendChild(option);
      });
    }
    function initProjectionMonths() {
      projectionMonth.innerHTML = '';
      analyticsMonth.innerHTML = '';
      projections.months.forEach(function(month) {
        const option = document.createElement('option');
        option.value = month.monthKey;
        option.textContent = month.monthName + ' ' + month.monthKey.slice(0, 4);
        projectionMonth.appendChild(option);
        analyticsMonth.appendChild(option.cloneNode(true));
      });
    }
    function showTab(tabName) {
      const showProjection = tabName === 'projection';
      const showAnalytics = tabName === 'analytics';
      document.querySelectorAll('.dashboard-panel').forEach(function(section) {
        section.hidden = showProjection || showAnalytics;
      });
      document.getElementById('projectionPanel').hidden = !showProjection;
      document.getElementById('analyticsPanel').hidden = !showAnalytics;
      document.querySelectorAll('.tab-button').forEach(function(button) {
        button.classList.toggle('active', button.getAttribute('data-tab') === tabName);
      });
      if (showProjection) renderProjection();
      if (showAnalytics) renderAnalytics();
    }
    function filteredTransactions() {
      const search = filters.search.value.trim().toLowerCase();
      const filtered = transactions.filter(function(row) {
        if (filters.month.value && row.monthKey !== filters.month.value) return false;
        if (filters.category.value && row.category !== filters.category.value) return false;
        if (filters.type.value && row.type !== filters.type.value) return false;
        if (filters.user.value && row.user !== filters.user.value) return false;
        if (filters.fixed.value && row.fixed !== filters.fixed.value) return false;
        if (filters.receipt.value === 'Con ticket' && !row.hasReceipt) return false;
        if (filters.receipt.value === 'Sin ticket' && row.hasReceipt) return false;
        if (movementFilters.category.value && row.category !== movementFilters.category.value) return false;
        if (movementFilters.type.value && row.type !== movementFilters.type.value) return false;
        if (movementFilters.store.value && row.store !== movementFilters.store.value) return false;
        if (movementFilters.user.value && row.user !== movementFilters.user.value) return false;
        if (search) {
          const haystack = [row.description, row.store, row.category, row.user].join(' ').toLowerCase();
          if (!haystack.includes(search)) return false;
        }
        return true;
      });
      if (movementFilters.amountSort.value === 'amount_asc') {
        return filtered.slice().sort(function(a, b) {
          if (a.amountCents !== b.amountCents) return a.amountCents - b.amountCents;
          return a.createdAt.localeCompare(b.createdAt);
        });
      }
      if (movementFilters.amountSort.value === 'amount_desc') {
        return filtered.slice().sort(function(a, b) {
          if (a.amountCents !== b.amountCents) return b.amountCents - a.amountCents;
          return b.createdAt.localeCompare(a.createdAt);
        });
      }
      return filtered.slice().sort(function(a, b) {
        return b.createdAt.localeCompare(a.createdAt);
      });
    }
    function centsToMoney(cents) { return money.format(cents / 100); }
    function setText(id, value) { document.getElementById(id).textContent = value; }
    function escapeHtml(value) {
      return String(value == null ? '' : value).replace(/[&<>"']/g, function(char) {
        return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;'}[char];
      });
    }
    function sum(rows, fn) {
      return rows.reduce(function(total, row) { return total + fn(row); }, 0);
    }
    function expenseRows(rows) {
      return rows.filter(function(row) { return row.kind === 'expense'; });
    }
    function previousMonthKey(monthKey) {
      const date = new Date(monthKey + '-01T00:00:00');
      date.setMonth(date.getMonth() - 1);
      return date.toISOString().slice(0, 7);
    }
    function monthTotals(monthKey) {
      const monthRows = transactions.filter(function(row) { return row.monthKey === monthKey; });
      const income = sum(monthRows.filter(function(row) { return row.kind === 'income'; }), function(row) { return row.amountCents; });
      const expense = sum(expenseRows(monthRows), function(row) { return row.amountCents; });
      return {income: income, expense: expense, balance: income - expense};
    }
    function setDelta(id, current, previous, goodWhenUp) {
      const node = document.getElementById(id);
      if (!node) return;
      if (previous == null) { node.textContent = ''; node.className = 'delta'; return; }
      const diff = current - previous;
      if (Math.abs(diff) < 1) {
        node.textContent = '→ igual que el mes anterior';
        node.className = 'delta flat';
        return;
      }
      const up = diff > 0;
      const positive = goodWhenUp ? up : !up;
      node.textContent = (up ? '↑ ' : '↓ ') + centsToMoney(Math.abs(diff)) + ' vs mes anterior';
      node.className = 'delta ' + (positive ? 'up' : 'down');
    }
    function renderSummary(rows) {
      const income = sum(rows.filter(function(row) { return row.kind === 'income'; }), function(row) { return row.amountCents; });
      const expense = sum(expenseRows(rows), function(row) { return row.amountCents; });
      const fixed = sum(expenseRows(rows).filter(function(row) { return row.isFixed; }), function(row) { return row.amountCents; });
      const variable = expense - fixed;
      const balance = income - expense;
      const coverage = rows.length ? Math.round((rows.filter(function(row) { return row.hasReceipt; }).length / rows.length) * 100) : 0;
      const pending = receipts.filter(function(row) { return ['nuevo', 'pending', 'voice_pending', 'dudoso'].includes(row.status); }).length;
      setText('incomeTotal', centsToMoney(income));
      setText('expenseTotal', centsToMoney(expense));
      setText('balanceTotal', centsToMoney(balance));
      setText('fixedTotal', centsToMoney(fixed));
      setText('variableTotal', centsToMoney(variable));
      setText('ticketCoverage', String(coverage) + '%');
      setText('pendingCount', String(pending));

      const balanceCard = document.getElementById('balanceCard');
      balanceCard.classList.remove('balance-pos', 'balance-neg');
      balanceCard.classList.add(balance < 0 ? 'balance-neg' : 'balance-pos');

      // Deltas y contexto solo cuando hay un mes concreto seleccionado.
      const selectedMonth = filters.month.value;
      const balanceMeta = document.getElementById('balanceMeta');
      if (selectedMonth && income > 0) {
        const ratio = Math.round((balance / income) * 100);
        balanceMeta.textContent = ratio + '% de los ingresos del mes';
        balanceMeta.className = 'delta ' + (balance < 0 ? 'down' : (ratio < 8 ? 'flat' : 'up'));
      } else {
        balanceMeta.textContent = '';
        balanceMeta.className = 'delta';
      }
      if (selectedMonth) {
        const prev = monthTotals(previousMonthKey(selectedMonth));
        const hasPrev = transactions.some(function(row) { return row.monthKey === previousMonthKey(selectedMonth); });
        setDelta('incomeDelta', income, hasPrev ? prev.income : null, true);
        setDelta('expenseDelta', expense, hasPrev ? prev.expense : null, false);
      } else {
        setDelta('incomeDelta', income, null, true);
        setDelta('expenseDelta', expense, null, false);
      }
      setText(
        'transactionsTotals',
        rows.length + ' movimientos · ingresos ' + centsToMoney(income) + ' · egresos ' + centsToMoney(expense) + ' · balance ' + centsToMoney(balance)
      );
    }
    function renderAlerts(rows) {
      const alertsList = document.getElementById('alertsList');
      const infoList = document.getElementById('infoList');
      const alerts = [];
      const info = [];
      const income = sum(rows.filter(function(row) { return row.kind === 'income'; }), function(row) { return row.amountCents; });
      const expense = sum(expenseRows(rows), function(row) { return row.amountCents; });
      const pending = receipts.filter(function(row) { return ['nuevo', 'pending', 'voice_pending', 'dudoso'].includes(row.status); });
      const missing = receipts.filter(function(row) { return row.status === 'missing'; });
      const withoutTicket = rows.filter(function(row) { return !row.hasReceipt; });
      const fixed = sum(expenseRows(rows).filter(function(row) { return row.isFixed; }), function(row) { return row.amountCents; });
      const categories = totalsBy(expenseRows(rows), 'category');
      const topCategory = categories[0];
      if (income - expense < 0) alerts.push({tone: 'danger', text: 'El balance filtrado está en negativo: ' + centsToMoney(income - expense) + '.'});
      if (missing.length) alerts.push({tone: 'danger', text: missing.length + ' archivo(s) faltan en la carpeta sincronizada.'});
      if (pending.length) alerts.push({tone: 'warn', text: pending.length + ' ticket(s) o audio(s) necesitan revisión.'});
      if (withoutTicket.length) alerts.push({tone: 'warn', text: withoutTicket.length + ' movimiento(s) no tienen ticket enlazado.'});
      if (topCategory) info.push({text: 'Mayor gasto por categoría: ' + topCategory.key + ' con ' + centsToMoney(topCategory.value) + '.'});
      if (fixed) info.push({text: 'Gastos fijos filtrados: ' + centsToMoney(fixed) + '.'});
      if (!alerts.length) alerts.push({tone: 'ok', text: 'Todo en orden: sin avisos para este filtro.'});
      if (!info.length) info.push({text: 'Sin datos adicionales para mostrar.'});
      alertsList.innerHTML = alerts.map(function(item) {
        return '<div class="notice ' + (item.tone === 'ok' ? '' : item.tone) + '">' + escapeHtml(item.text) + '</div>';
      }).join('');
      infoList.innerHTML = info.map(function(item) {
        return '<div class="notice">' + escapeHtml(item.text) + '</div>';
      }).join('');
    }
    function totalsBy(rows, key) {
      const map = new Map();
      rows.forEach(function(row) {
        const label = row[key] || 'Sin dato';
        map.set(label, (map.get(label) || 0) + row.amountCents);
      });
      return Array.from(map.entries()).map(function(entry) {
        return {key: entry[0], value: entry[1]};
      }).sort(function(a, b) { return b.value - a.value; });
    }
    function renderIncomeList(rows) {
      const list = document.getElementById('incomeList');
      const incomeRows = rows.filter(function(row) { return row.kind === 'income'; });
      const total = sum(incomeRows, function(row) { return row.amountCents; });
      setText('incomeListTotal', centsToMoney(total));
      if (!incomeRows.length) {
        list.innerHTML = '<div class="muted">Sin ingresos en este filtro.</div>';
        return;
      }
      list.innerHTML = incomeRows.map(function(row) {
        const label = [row.date, row.description, row.store].filter(Boolean).join(' · ');
        return '<div class="notice"><strong>' + escapeHtml(label) + '</strong><br><span class="income">' + centsToMoney(row.amountCents) + '</span></div>';
      }).join('');
    }
    function renderTable(rows) {
      const body = document.getElementById('transactionsBody');
      const colCount = editable ? 10 : 9;
      if (!rows.length) {
        body.innerHTML = '<tr><td colspan="' + colCount + '" class="muted">No hay movimientos para los filtros seleccionados.</td></tr>';
        return;
      }
      body.innerHTML = rows.map(function(row) {
        const source = row.receiptUrl
          ? '<a href="' + row.receiptUrl + '" title="Abrir ticket">📎</a>'
          : '<span class="muted" title="Sin ticket">—</span>';
        const action = editable
          ? '<td><button class="linkish" type="button" data-edit-id="' + row.id + '">Editar</button></td>'
          : '';
        return '<tr>' +
          '<td>' + escapeHtml(row.date) + '</td>' +
          '<td class="description">' + escapeHtml(row.description) + '</td>' +
          '<td>' + escapeHtml(row.category) + '</td>' +
          '<td class="amount ' + (row.kind === 'income' ? 'income' : 'expense') + '">' + escapeHtml(row.amount) + '</td>' +
          '<td>' + escapeHtml(row.type) + '</td>' +
          '<td>' + escapeHtml(row.store) + '</td>' +
          '<td>' + escapeHtml(row.user) + '</td>' +
          '<td>' + escapeHtml(row.fixed) + '</td>' +
          '<td style="text-align:center">' + source + '</td>' +
          action +
        '</tr>';
      }).join('');
    }
    function getTransactionById(id) {
      return transactions.find(function(row) { return String(row.id) === String(id); });
    }
    function amountInput(row) {
      return (row.amountCents / 100).toFixed(2).replace('.', ',');
    }
    function showEditError(message) {
      const error = document.getElementById('editError');
      error.textContent = message;
      error.hidden = !message;
    }
    function openEditModal(row) {
      if (!editable) {
        alert('Para editar abre el panel local con: python scripts/serve_dashboard.py');
        return;
      }
      showEditError('');
      document.getElementById('editId').value = row.id;
      document.getElementById('editDate').value = row.dateIso;
      document.getElementById('editAmount').value = amountInput(row);
      document.getElementById('editDescription').value = row.description || '';
      document.getElementById('editCategory').value = row.category || '';
      document.getElementById('editKind').value = row.kind;
      document.getElementById('editStore').value = row.store || '';
      document.getElementById('editFixed').value = row.isFixed ? 'true' : 'false';
      document.getElementById('editModal').hidden = false;
      document.getElementById('editDescription').focus();
    }
    function closeEditModal() {
      document.getElementById('editModal').hidden = true;
      showEditError('');
    }
    async function submitEdit(event) {
      event.preventDefault();
      const id = document.getElementById('editId').value;
      const payload = {
        date: document.getElementById('editDate').value,
        amount: document.getElementById('editAmount').value,
        description: document.getElementById('editDescription').value,
        category: document.getElementById('editCategory').value,
        kind: document.getElementById('editKind').value,
        store: document.getElementById('editStore').value,
        isFixed: document.getElementById('editFixed').value === 'true'
      };
      try {
        const response = await fetch('/api/transactions/' + encodeURIComponent(id), {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload)
        });
        const result = await response.json().catch(function() { return {}; });
        if (!response.ok || !result.ok) {
          throw new Error(result.error || 'No se pudo guardar el movimiento.');
        }
        window.location.reload();
      } catch (error) {
        showEditError(error.message || 'No se pudo guardar el movimiento.');
      }
    }
    function projectionRowsForMonth(month) {
      return projections.rows.filter(function(row) { return row.month === month && row.status !== 'skipped'; });
    }
    function projectionSummaryForMonth(month) {
      return projections.months.find(function(row) { return row.monthKey === month; }) || null;
    }
    function getProjectionRow(templateId, month) {
      return projections.rows.find(function(row) {
        return String(row.templateId) === String(templateId) && row.month === month;
      });
    }
    function renderProjection() {
      drawTrendChart();
      const month = projectionMonth.value || (projections.months[0] && projections.months[0].monthKey) || '';
      const summary = projectionSummaryForMonth(month);
      const body = document.getElementById('projectionBody');
      if (!summary) {
        setText('projectionIncome', '0,00 €');
        setText('projectionExpense', '0,00 €');
        setText('projectionBalance', '0,00 €');
        setText('projectionRemainingBalance', '0,00 €');
        setText('projectionActualBalance', '0,00 €');
        renderProjectionCashflow([]);
        body.innerHTML = '<tr><td colspan="9" class="muted">Aún no hay proyecciones cargadas.</td></tr>';
        return;
      }
      setText('projectionIncome', summary.projectedIncome);
      setText('projectionExpense', summary.projectedExpense);
      setText('projectionBalance', summary.projectedBalance);
      setText('projectionActualBalance', summary.actualBalance);

      const rows = projectionRowsForMonth(month);
      const totals = renderProjectionCashflow(rows);
      setText('projectionRemainingBalance', centsToMoney(totals.pendingIncome - totals.pendingExpense));
      if (!rows.length) {
        body.innerHTML = '<tr><td colspan="9" class="muted">No hay proyecciones para este mes.</td></tr>';
        return;
      }
      body.innerHTML = rows.map(function(row) {
        const amountClass = row.kind === 'income' ? 'income' : 'expense';
        const statusClass = 'status-' + row.status;
        const action = editable
          ? quickStatusButton(row, {withLabel: true}) + ' ' +
            '<button class="linkish" type="button" data-projection-id="' + row.templateId + '" data-projection-month="' + row.month + '">Editar</button> ' +
            '<button class="linkish" type="button" data-delete-projection-id="' + row.templateId + '" data-projection-month="' + row.month + '">Borrar</button>'
          : '<span class="muted">—</span>';
        return '<tr>' +
          '<td class="description">' + escapeHtml(row.name) + '</td>' +
          '<td>' + escapeHtml(row.type) + '</td>' +
          '<td>' + escapeHtml(row.category) + '</td>' +
          '<td>' + escapeHtml(row.installmentLabel) + '</td>' +
          '<td>' + escapeHtml(row.remainingLabel) + '</td>' +
          '<td class="amount ' + amountClass + '">' + escapeHtml(row.amount) + '</td>' +
          '<td><span class="status-chip ' + statusClass + '">' + escapeHtml(row.statusLabel) + '</span></td>' +
          '<td class="description">' + escapeHtml(row.note) + '</td>' +
          '<td>' + action + '</td>' +
        '</tr>';
      }).join('');
    }
    function statusLabelFor(kind, status) {
      if (status === 'completed') return kind === 'income' ? 'Cobrado' : 'Pagado';
      if (status === 'skipped') return 'Omitido';
      return 'Pendiente';
    }
    function quickStatusButton(row, options) {
      options = options || {};
      const attrs = ' data-status-id="' + row.templateId + '" data-status-month="' + row.month + '"';
      if (row.status === 'pending') {
        const label = options.withLabel ? '✓ ' + (row.kind === 'income' ? 'Cobrar' : 'Pagar') : '✓';
        const title = row.kind === 'income' ? 'Marcar como cobrado' : 'Marcar como pagado';
        return '<button class="quick-status" type="button" title="' + title + '"' + attrs + ' data-status-next="completed">' + label + '</button>';
      }
      const undoLabel = options.withLabel ? '↩ Pendiente' : '↩';
      return '<button class="quick-status undo" type="button" title="Volver a pendiente"' + attrs + ' data-status-next="pending">' + undoLabel + '</button>';
    }
    async function setProjectionStatus(templateId, month, status, button) {
      if (!editable) {
        alert('Para editar abre el panel local con: python scripts/serve_dashboard.py');
        return;
      }
      if (button) button.disabled = true;
      try {
        const url = '/api/projections/' + encodeURIComponent(templateId) + '/' + encodeURIComponent(month) + '/status';
        const response = await fetch(url, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({status: status})
        });
        const result = await response.json().catch(function() { return {}; });
        if (!response.ok || !result.ok) {
          throw new Error(result.error || 'No se pudo cambiar el estado.');
        }
        const row = getProjectionRow(templateId, month);
        if (row) {
          row.status = status;
          row.statusLabel = statusLabelFor(row.kind, status);
        }
        renderProjection();
      } catch (error) {
        alert(error.message || 'No se pudo cambiar el estado.');
        if (button) button.disabled = false;
      }
    }
    function handleStatusClick(event) {
      const button = event.target.closest('[data-status-id]');
      if (!button) return false;
      setProjectionStatus(
        button.getAttribute('data-status-id'),
        button.getAttribute('data-status-month'),
        button.getAttribute('data-status-next') || 'completed',
        button
      );
      return true;
    }
    function cashflowTotal(rows) {
      return sum(rows, function(row) { return row.amountCents || 0; });
    }
    function rowsGroupedByCategory(rows) {
      const groups = new Map();
      rows.forEach(function(row) {
        const category = row.category || 'Sin categoria';
        if (!groups.has(category)) groups.set(category, []);
        groups.get(category).push(row);
      });
      return Array.from(groups.entries()).map(function(entry) {
        return {
          category: entry[0],
          rows: entry[1],
          totalCents: cashflowTotal(entry[1]),
        };
      }).sort(function(left, right) {
        if (right.totalCents !== left.totalCents) return right.totalCents - left.totalCents;
        return left.category.localeCompare(right.category, 'es');
      });
    }
    function renderCashflowRows(rows) {
      return rows.map(function(row) {
        const tone = row.kind === 'income' ? 'income' : 'expense';
        const quick = editable && !row.syntheticCashflow ? quickStatusButton(row) : '';
        return '<div class="cashflow-item">' +
          '<span>' + escapeHtml(row.name) + (row.note ? '<br><small class="muted">' + escapeHtml(row.note) + '</small>' : '') + '</span>' +
          '<span class="cashflow-actions"><strong class="' + tone + '">' + escapeHtml(row.amount) + '</strong>' + quick + '</span>' +
        '</div>';
      }).join('');
    }
    function cashflowBudgetRow(row, amountCents, label, note) {
      return Object.assign({}, row, {
        name: label,
        amountCents: amountCents,
        amount: centsToMoney(amountCents),
        note: note || '',
        syntheticCashflow: true
      });
    }
    function renderCashflowList(id, totalId, rows, options) {
      options = options || {};
      const container = document.getElementById(id);
      const total = cashflowTotal(rows);
      setText(totalId, centsToMoney(total));
      if (!rows.length) {
        container.innerHTML = '<div class="muted" style="padding: 10px;">Sin movimientos.</div>';
        return total;
      }
      if (options.groupByCategory) {
        container.innerHTML = rowsGroupedByCategory(rows).map(function(group) {
          return '<section class="cashflow-group">' +
            '<div class="cashflow-group-header">' +
              '<span>' + escapeHtml(group.category) + '</span>' +
              '<strong>' + escapeHtml(centsToMoney(group.totalCents)) + '</strong>' +
            '</div>' +
            '<div class="cashflow-group-items">' + renderCashflowRows(group.rows) + '</div>' +
          '</section>';
        }).join('');
        return total;
      }
      container.innerHTML = renderCashflowRows(rows);
      return total;
    }
    function renderProjectionCashflow(rows) {
      const collectedIncomeRows = rows.filter(function(row) { return row.kind === 'income' && row.status === 'completed'; });
      const pendingIncomeRows = rows.filter(function(row) { return row.kind === 'income' && row.status === 'pending'; });
      const trackedExpenseRows = rows.filter(function(row) { return row.kind === 'expense' && row.tracksActualCategory; });
      const manualExpenseRows = rows.filter(function(row) { return row.kind === 'expense' && !row.tracksActualCategory; });
      const paidExpenseRows = manualExpenseRows
        .filter(function(row) { return row.status === 'completed'; })
        .concat(trackedExpenseRows.map(function(row) {
          return cashflowBudgetRow(
            row,
            row.actualSpentCents || 0,
            row.name + ' · gastado real',
            'Registrado en movimientos del mes'
          );
        }));
      const pendingExpenseRows = manualExpenseRows
        .filter(function(row) { return row.status === 'pending'; })
        .concat(trackedExpenseRows.map(function(row) {
          return cashflowBudgetRow(
            row,
            row.remainingBudgetCents || 0,
            row.name + ' · restante',
            'Presupuesto mensual menos gasto real'
          );
        }));
      return {
        collectedIncome: renderCashflowList(
          'collectedIncomeList',
          'collectedIncomeTotal',
          collectedIncomeRows
        ),
        pendingIncome: renderCashflowList(
          'pendingIncomeList',
          'pendingIncomeTotal',
          pendingIncomeRows,
          {groupByCategory: true}
        ),
        paidExpense: renderCashflowList(
          'paidExpenseList',
          'paidExpenseTotal',
          paidExpenseRows
        ),
        pendingExpense: renderCashflowList(
          'pendingExpenseList',
          'pendingExpenseTotal',
          pendingExpenseRows,
          {groupByCategory: true}
        )
      };
    }
    function monthRows(month) {
      return transactions.filter(function(row) { return row.monthKey === month; });
    }
    function ratioText(numerator, denominator) {
      if (!denominator) return '0%';
      return Math.round((numerator / denominator) * 100) + '%';
    }
    function currentMonthAnalysis(month) {
      const actualRows = monthRows(month);
      const expense = sum(expenseRows(actualRows), function(row) { return row.amountCents; });
      const income = sum(actualRows.filter(function(row) { return row.kind === 'income'; }), function(row) { return row.amountCents; });
      const fixed = sum(expenseRows(actualRows).filter(function(row) { return row.isFixed; }), function(row) { return row.amountCents; });
      const variable = expense - fixed;
      const categories = totalsBy(expenseRows(actualRows), 'category');
      const stores = totalsBy(expenseRows(actualRows).filter(function(row) { return row.store; }), 'store');
      const summary = projectionSummaryForMonth(month) || {
        monthKey: month,
        monthName: month,
        projectedIncomeCents: income,
        projectedExpenseCents: expense,
        projectedBalanceCents: income - expense,
        actualIncomeCents: income,
        actualExpenseCents: expense,
        actualBalanceCents: income - expense
      };
      return {
        month: month,
        rows: actualRows,
        income: income,
        expense: expense,
        fixed: fixed,
        variable: variable,
        topCategory: categories[0] || null,
        topStore: stores[0] || null,
        summary: summary
      };
    }
    function futureRiskMonths() {
      return projections.months.filter(function(month) {
        const income = month.projectedIncomeCents || 0;
        const balance = month.projectedBalanceCents || 0;
        return balance < 0 || (income > 0 && balance < income * 0.08);
      });
    }
    function nearFinishedInstallments(month) {
      return projectionRowsForMonth(month).filter(function(row) {
        return row.kind === 'expense' && row.remainingInstallments != null && row.remainingInstallments >= 0 && row.remainingInstallments <= 2;
      });
    }
    function recommendationHtml(item) {
      return '<div class="notice ' + (item.tone || '') + '">' +
        '<span class="recommendation-title">' + escapeHtml(item.title) + '</span>' +
        escapeHtml(item.text) +
      '</div>';
    }
    function aiAlertsForMonth(month) {
      const monthRows = transactions.filter(function(row) { return row.monthKey === month; });
      const alerts = [];
      monthRows.forEach(function(row) {
        (row.inferenceNotes || []).forEach(function(note) {
          alerts.push({
            tone: 'warn',
            title: '#' + row.id + ' · ' + (row.description || row.sourceText || row.category),
            text: note + ' Se registró como ' + row.category + '. Si no corresponde, edítalo.'
          });
        });

        const normalizedText = normalizeText(
          [row.description, row.sourceText, row.store, row.category].filter(Boolean).join(' ')
        );
        const looksRecurringExpense = row.kind === 'expense' && (
          row.isFixed ||
          ['suscripcion', 'suscripciones', 'hosting', 'sered', 'hostinger', 'claude', 'chatgpt', 'alquiler', 'internet', 'seguro', 'cuota'].some(function(token) {
            return normalizedText.includes(token);
          })
        );
        const looksSalaryIncome = row.kind === 'income' && (
          ['sueldo', 'nomina', 'cobro'].some(function(token) {
            return normalizedText.includes(token);
          })
        );

        if ((looksRecurringExpense || looksSalaryIncome) && !row.projectionTemplateId) {
          alerts.push({
            tone: 'warn',
            title: '#' + row.id + ' · sin cruce con proyección',
            text: 'No pude marcar automáticamente qué pago/cobro del mes corresponde a "' + (row.description || row.category) + '". Ahora quedó en ' + row.category + '; revísalo si era otro concepto.'
          });
        }
      });
      return alerts;
    }
    function renderAnalytics() {
      const month = analyticsMonth.value || (projections.months[0] && projections.months[0].monthKey) || '';
      if (!month) {
        document.getElementById('analyticsNarrative').innerHTML = '<div class="muted">Aun no hay datos para analizar.</div>';
        document.getElementById('analyticsRecommendations').innerHTML = '<div class="muted">Carga movimientos o proyecciones para generar recomendaciones.</div>';
        document.getElementById('analyticsAiAlerts').innerHTML = '<div class="muted">Sin avisos.</div>';
        setText('analyticsAiAlertsCount', 'Sin avisos');
        document.getElementById('analyticsFutureBody').innerHTML = '<tr><td colspan="5" class="muted">Sin proyecciones.</td></tr>';
        return;
      }

      const analysis = currentMonthAnalysis(month);
      const summary = analysis.summary;
      const projectedIncome = summary.projectedIncomeCents || 0;
      const projectedExpense = summary.projectedExpenseCents || 0;
      const projectedBalance = summary.projectedBalanceCents || 0;
      const fixedRatio = projectedIncome ? analysis.fixed / projectedIncome : 0;
      const marginRatio = projectedIncome ? projectedBalance / projectedIncome : 0;
      const pendingReceipts = receipts.filter(function(row) {
        return ['nuevo', 'pending', 'voice_pending', 'dudoso'].includes(row.status);
      });
      const clientAdvances = sum(expenseRows(analysis.rows).filter(function(row) {
        return row.category === 'Ingresos clientes';
      }), function(row) { return row.amountCents; });
      const clientIncome = sum(analysis.rows.filter(function(row) {
        return row.kind === 'income' && row.category === 'Ingresos clientes';
      }), function(row) { return row.amountCents; });
      const riskyMonths = futureRiskMonths();
      const endingInstallments = nearFinishedInstallments(month);

      let score = 100;
      if (projectedBalance < 0) score -= 35;
      else if (marginRatio < 0.08) score -= 18;
      else if (marginRatio < 0.15) score -= 8;
      if (fixedRatio > 0.7) score -= 22;
      else if (fixedRatio > 0.5) score -= 10;
      if (pendingReceipts.length) score -= Math.min(12, pendingReceipts.length * 4);
      if (analysis.topCategory && projectedIncome && analysis.topCategory.value > projectedIncome * 0.25) score -= 8;
      if (riskyMonths.length) score -= Math.min(16, riskyMonths.length * 4);
      score = Math.max(0, Math.min(100, Math.round(score)));

      const scoreNode = document.getElementById('analyticsScore');
      scoreNode.textContent = score + '/100';
      scoreNode.className = score >= 75 ? 'score-good' : (score >= 50 ? 'score-warn' : 'score-danger');
      setText('analyticsProjectedBalance', centsToMoney(projectedBalance));
      setText('analyticsSafetyMargin', ratioText(projectedBalance, projectedIncome));
      setText('analyticsFixedRatio', ratioText(analysis.fixed, projectedIncome));
      setText('analyticsTopExpense', analysis.topCategory ? analysis.topCategory.key + ' ' + centsToMoney(analysis.topCategory.value) : 'Sin datos');
      setText('analyticsRiskMonths', String(riskyMonths.length));

      const narrative = [];
      narrative.push({
        tone: projectedBalance < 0 ? 'danger' : (marginRatio < 0.08 ? 'warn' : ''),
        title: 'Balance esperado',
        text: 'Para este mes el balance proyectado es ' + centsToMoney(projectedBalance) + ' sobre ingresos de ' + centsToMoney(projectedIncome) + '.'
      });
      narrative.push({
        tone: fixedRatio > 0.7 ? 'danger' : (fixedRatio > 0.5 ? 'warn' : ''),
        title: 'Peso de gastos fijos',
        text: 'Los gastos fijos registrados representan ' + ratioText(analysis.fixed, projectedIncome) + ' de los ingresos proyectados del mes.'
      });
      if (analysis.topCategory) {
        narrative.push({
          tone: '',
          title: 'Concentracion de gasto',
          text: 'La categoria con mas gasto real es ' + analysis.topCategory.key + ' con ' + centsToMoney(analysis.topCategory.value) + '.'
        });
      }
      if (analysis.topStore) {
        narrative.push({
          tone: '',
          title: 'Tienda principal',
          text: 'La tienda con mayor gasto real es ' + analysis.topStore.key + ' con ' + centsToMoney(analysis.topStore.value) + '.'
        });
      }
      if (!analysis.rows.length) {
        narrative.push({
          tone: 'warn',
          title: 'Sin movimientos reales',
          text: 'No hay movimientos registrados para este mes; el analisis se apoya solo en la proyeccion.'
        });
      }
      document.getElementById('analyticsNarrative').innerHTML = narrative.map(recommendationHtml).join('');

      const recommendations = [];
      if (projectedBalance < 0) {
        recommendations.push({
          tone: 'danger',
          title: 'Prioridad: cerrar el deficit',
          text: 'Antes de asumir nuevos gastos, reduce o aplaza al menos ' + centsToMoney(Math.abs(projectedBalance)) + ' para dejar el mes en cero.'
        });
      } else if (marginRatio < 0.08) {
        recommendations.push({
          tone: 'warn',
          title: 'Margen demasiado justo',
          text: 'Intenta reservar un colchon minimo del 8-10% de ingresos. Para este mes eso seria acercarte a ' + centsToMoney(Math.round(projectedIncome * 0.1)) + ' libres.'
        });
      } else {
        recommendations.push({
          tone: '',
          title: 'Aprovecha el margen',
          text: 'Si el mes se mantiene asi, separa una parte del balance positivo para ahorro, deuda o gastos futuros antes de aumentar ocio o compras variables.'
        });
      }
      if (fixedRatio > 0.5) {
        recommendations.push({
          tone: fixedRatio > 0.7 ? 'danger' : 'warn',
          title: 'Audita gastos fijos',
          text: 'Los fijos pesan mucho. Revisa suscripciones, deudas y servicios recurrentes; cada baja ahi mejora todos los meses siguientes.'
        });
      }
      if (analysis.topCategory && analysis.topCategory.value > Math.max(analysis.variable * 0.35, projectedIncome * 0.12)) {
        recommendations.push({
          tone: 'warn',
          title: 'Controla ' + analysis.topCategory.key,
          text: 'Esa categoria concentra bastante gasto. Ponle un limite semanal y revisala antes de registrar nuevas compras similares.'
        });
      }
      if (pendingReceipts.length) {
        recommendations.push({
          tone: 'warn',
          title: 'Procesa pendientes',
          text: 'Hay ' + pendingReceipts.length + ' ticket(s) o audio(s) pendientes. Registrarlos puede cambiar el balance y evitar decisiones con datos incompletos.'
        });
      }
      if (clientAdvances > clientIncome) {
        recommendations.push({
          tone: 'warn',
          title: 'Reembolsos de clientes',
          text: 'Este mes hay ' + centsToMoney(clientAdvances - clientIncome) + ' mas en adelantos a clientes que en cobros de esa categoria. Conviene reclamar o separar seguimiento.'
        });
      }
      if (endingInstallments.length) {
        recommendations.push({
          tone: '',
          title: 'Cuotas cerca de terminar',
          text: endingInstallments.length + ' cuota(s) estan cerca de acabar. Cuando terminen, redirige ese importe a ahorro o a bajar deuda en vez de absorberlo como gasto nuevo.'
        });
      }
      if (riskyMonths.length) {
        recommendations.push({
          tone: 'warn',
          title: 'Mira los proximos meses',
          text: 'Hay ' + riskyMonths.length + ' mes(es) con deficit o margen bajo. Revisa especialmente ' + riskyMonths.slice(0, 3).map(function(row) { return row.monthName; }).join(', ') + '.'
        });
      }
      document.getElementById('analyticsRecommendations').innerHTML = recommendations.map(recommendationHtml).join('');

      const aiAlerts = aiAlertsForMonth(month);
      setText(
        'analyticsAiAlertsCount',
        aiAlerts.length ? (aiAlerts.length + ' aviso(s)') : 'Sin avisos'
      );
      document.getElementById('analyticsAiAlerts').innerHTML = aiAlerts.length
        ? aiAlerts.map(recommendationHtml).join('')
        : '<div class="muted">La IA no dejó dudas relevantes en este mes.</div>';

      document.getElementById('analyticsFutureBody').innerHTML = projections.months.slice(0, 6).map(function(row) {
        const balance = row.projectedBalanceCents || 0;
        const income = row.projectedIncomeCents || 0;
        const tone = balance < 0 ? 'danger' : (income > 0 && balance < income * 0.08 ? 'warn' : '');
        const label = balance < 0
          ? 'Riesgo: deficit proyectado'
          : (income > 0 && balance < income * 0.08 ? 'Margen bajo' : 'Saludable si se cumple la proyeccion');
        return '<tr>' +
          '<td>' + escapeHtml(row.monthName) + '</td>' +
          '<td class="amount income">' + escapeHtml(row.projectedIncome) + '</td>' +
          '<td class="amount expense">' + escapeHtml(row.projectedExpense) + '</td>' +
          '<td class="amount ' + (balance >= 0 ? 'income' : 'expense') + '">' + escapeHtml(row.projectedBalance) + '</td>' +
          '<td><span class="notice ' + tone + '">' + escapeHtml(label) + '</span></td>' +
        '</tr>';
      }).join('');
    }
    function showProjectionError(message) {
      const error = document.getElementById('projectionError');
      error.textContent = message;
      error.hidden = !message;
    }
    function openProjectionModal(row) {
      if (!editable) {
        alert('Para editar abre el panel local con: python scripts/serve_dashboard.py');
        return;
      }
      showProjectionError('');
      document.getElementById('projectionTemplateId').value = row.templateId;
      document.getElementById('projectionEditMonth').value = row.month;
      document.getElementById('projectionName').value = row.name || '';
      document.getElementById('projectionKind').value = row.kind || 'expense';
      document.getElementById('projectionCategory').value = row.category || '';
      document.getElementById('projectionAmount').value = amountInput(row);
      document.getElementById('projectionStatus').value = row.status;
      document.getElementById('projectionDuration').value = row.installmentTotal ? (row.installmentTotal === 1 ? 'once' : 'installments') : 'monthly';
      document.getElementById('projectionInstallmentCurrent').value = row.installmentCurrent || '';
      document.getElementById('projectionInstallmentTotal').value = row.installmentTotal || '';
      document.getElementById('projectionUpdateDefault').value = 'false';
      document.getElementById('projectionNote').value = row.storedNote || row.note || '';
      document.getElementById('projectionModal').hidden = false;
      document.getElementById('projectionAmount').focus();
    }
    function openNewProjectionModal() {
      if (!editable) {
        alert('Para editar abre el panel local con: python scripts/serve_dashboard.py');
        return;
      }
      const month = projectionMonth.value || (projections.months[0] && projections.months[0].monthKey) || '';
      showProjectionError('');
      document.getElementById('projectionTemplateId').value = 'new';
      document.getElementById('projectionEditMonth').value = month;
      document.getElementById('projectionName').value = '';
      document.getElementById('projectionKind').value = 'expense';
      document.getElementById('projectionCategory').value = 'Hogar y Alimentación';
      document.getElementById('projectionAmount').value = '';
      document.getElementById('projectionStatus').value = 'pending';
      document.getElementById('projectionDuration').value = 'monthly';
      document.getElementById('projectionInstallmentCurrent').value = '';
      document.getElementById('projectionInstallmentTotal').value = '';
      document.getElementById('projectionUpdateDefault').value = 'true';
      document.getElementById('projectionNote').value = '';
      document.getElementById('projectionModal').hidden = false;
      document.getElementById('projectionName').focus();
    }
    function closeProjectionModal() {
      document.getElementById('projectionModal').hidden = true;
      showProjectionError('');
    }
    async function submitProjectionEdit(event) {
      event.preventDefault();
      const id = document.getElementById('projectionTemplateId').value;
      const month = document.getElementById('projectionEditMonth').value;
      const payload = {
        name: document.getElementById('projectionName').value,
        kind: document.getElementById('projectionKind').value,
        category: document.getElementById('projectionCategory').value,
        amount: document.getElementById('projectionAmount').value,
        status: document.getElementById('projectionStatus').value,
        duration: document.getElementById('projectionDuration').value,
        installmentCurrent: document.getElementById('projectionInstallmentCurrent').value,
        installmentTotal: document.getElementById('projectionInstallmentTotal').value,
        updateDefault: document.getElementById('projectionUpdateDefault').value === 'true',
        note: document.getElementById('projectionNote').value,
        month: month
      };
      try {
        const url = id === 'new'
          ? '/api/projections'
          : '/api/projections/' + encodeURIComponent(id) + '/' + encodeURIComponent(month);
        const response = await fetch(url, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload)
        });
        const result = await response.json().catch(function() { return {}; });
        if (!response.ok || !result.ok) {
          throw new Error(result.error || 'No se pudo guardar la proyeccion.');
        }
        window.location.reload();
      } catch (error) {
        showProjectionError(error.message || 'No se pudo guardar la proyeccion.');
      }
    }
    async function deleteProjectionItem(templateId, month) {
      if (!editable) {
        alert('Para editar abre el panel local con: python scripts/serve_dashboard.py');
        return;
      }
      if (!confirm('Borrar este item solo del mes seleccionado?')) return;
      const response = await fetch('/api/projections/' + encodeURIComponent(templateId) + '/' + encodeURIComponent(month), {
        method: 'DELETE'
      });
      const result = await response.json().catch(function() { return {}; });
      if (!response.ok || !result.ok) {
        alert(result.error || 'No se pudo borrar el item.');
        return;
      }
      window.location.reload();
    }
    function chartBase(canvas) {
      const rect = canvas.getBoundingClientRect();
      const ratio = window.devicePixelRatio || 1;
      canvas.width = Math.max(300, Math.floor(rect.width * ratio));
      canvas.height = Math.max(200, Math.floor(rect.height * ratio));
      const ctx = canvas.getContext('2d');
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      ctx.clearRect(0, 0, rect.width, rect.height);
      return {ctx: ctx, width: rect.width, height: rect.height};
    }
    function drawTrendChart() {
      const canvas = document.getElementById('trendChart');
      if (!canvas || canvas.offsetParent === null) return;
      const base = chartBase(canvas);
      const months = projections.months || [];
      const ctx = base.ctx;
      const pad = {left: 62, right: 16, top: 20, bottom: 36};
      const innerW = base.width - pad.left - pad.right;
      const innerH = base.height - pad.top - pad.bottom;
      if (!months.length) {
        ctx.fillStyle = '#60706a';
        ctx.font = '12px system-ui';
        ctx.fillText('Sin proyecciones cargadas.', pad.left, pad.top + 20);
        return;
      }
      const hasActuals = function(month) {
        return (month.actualIncomeCents || 0) !== 0 || (month.actualExpenseCents || 0) !== 0;
      };
      const balances = months.map(function(m) { return m.projectedBalanceCents || 0; })
        .concat(months.filter(hasActuals).map(function(m) { return m.actualBalanceCents || 0; }));
      const maxValue = Math.max(
        1,
        ...months.map(function(m) { return Math.max(m.projectedIncomeCents || 0, m.projectedExpenseCents || 0); }),
        ...balances
      );
      const minValue = Math.min(0, ...balances);
      const yFor = function(value) {
        return pad.top + ((maxValue - value) / (maxValue - minValue)) * innerH;
      };
      ctx.font = '11px system-ui';
      // Líneas guía horizontales con importes.
      const steps = 4;
      for (let i = 0; i <= steps; i++) {
        const value = maxValue - ((maxValue - minValue) / steps) * i;
        const y = yFor(value);
        ctx.strokeStyle = '#eef2ee';
        ctx.beginPath();
        ctx.moveTo(pad.left, y);
        ctx.lineTo(pad.left + innerW, y);
        ctx.stroke();
        ctx.fillStyle = '#60706a';
        ctx.fillText(Math.round(value / 100) + ' €', 8, y + 4);
      }
      const zeroY = yFor(0);
      ctx.strokeStyle = '#c8d2ca';
      ctx.beginPath();
      ctx.moveTo(pad.left, zeroY);
      ctx.lineTo(pad.left + innerW, zeroY);
      ctx.stroke();
      const groupW = innerW / months.length;
      const barW = Math.max(4, Math.min(16, groupW * 0.3));
      const centerFor = function(index) { return pad.left + index * groupW + groupW / 2; };
      months.forEach(function(month, index) {
        const center = centerFor(index);
        const incomeH = zeroY - yFor(month.projectedIncomeCents || 0);
        const expenseH = zeroY - yFor(month.projectedExpenseCents || 0);
        ctx.fillStyle = '#1f7a5a';
        ctx.fillRect(center - barW - 1, zeroY - incomeH, barW, incomeH);
        ctx.fillStyle = '#b34545';
        ctx.fillRect(center + 1, zeroY - expenseH, barW, expenseH);
        ctx.fillStyle = '#60706a';
        const label = month.monthKey.slice(5) + '/' + month.monthKey.slice(2, 4);
        ctx.fillText(label, center - ctx.measureText(label).width / 2, pad.top + innerH + 16);
      });
      // Línea de balance proyectado.
      ctx.strokeStyle = '#2c6387';
      ctx.lineWidth = 2;
      ctx.beginPath();
      months.forEach(function(month, index) {
        const x = centerFor(index);
        const y = yFor(month.projectedBalanceCents || 0);
        if (index === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.stroke();
      ctx.lineWidth = 1;
      // Puntos de balance real solo en meses con movimientos.
      months.forEach(function(month, index) {
        if (!hasActuals(month)) return;
        const x = centerFor(index);
        const y = yFor(month.actualBalanceCents || 0);
        ctx.fillStyle = '#17211d';
        ctx.beginPath();
        ctx.arc(x, y, 4, 0, Math.PI * 2);
        ctx.fill();
        const amountLabel = centsToMoney(month.actualBalanceCents || 0);
        ctx.fillText(amountLabel, x - ctx.measureText(amountLabel).width / 2, y - 9);
      });
    }
    function drawDailyExpenseChart(rows) {
      const canvas = document.getElementById('monthlyChart');
      const base = chartBase(canvas);
      const expenseData = expenseRows(rows);
      const selectedMonth = filters.month.value || unique(expenseData.map(function(row) { return row.monthKey; })).slice(-1)[0] || '';
      const chartRows = selectedMonth
        ? expenseData.filter(function(row) { return row.monthKey === selectedMonth; })
        : expenseData;
      const totalsByDay = new Map();
      chartRows.forEach(function(row) {
        const label = row.date;
        totalsByDay.set(label, (totalsByDay.get(label) || 0) + row.amountCents);
      });
      const data = Array.from(totalsByDay.entries()).map(function(entry) {
        return { label: entry[0], value: entry[1] };
      }).sort(function(a, b) {
        const [dayA, monthA, yearA] = a.label.split('/').map(Number);
        const [dayB, monthB, yearB] = b.label.split('/').map(Number);
        return new Date(yearA, monthA - 1, dayA).getTime() - new Date(yearB, monthB - 1, dayB).getTime();
      });
      setText(
        'dailyExpenseMeta',
        (selectedMonth || 'Sin mes') + ' · total ' + centsToMoney(sum(chartRows, function(row) { return row.amountCents; }))
      );
      drawDailyBars(base.ctx, base.width, base.height, data);
    }
    function drawCategoryChart(rows) {
      const canvas = document.getElementById('categoryChart');
      const base = chartBase(canvas);
      drawHorizontalBars(base.ctx, base.width, base.height, totalsBy(expenseRows(rows), 'category').slice(0, 8));
    }
    function drawDailyBars(ctx, width, height, data) {
      const pad = {left: 52, right: 20, top: 24, bottom: 48};
      const innerW = width - pad.left - pad.right;
      const innerH = height - pad.top - pad.bottom;
      const max = Math.max(1, ...data.map(function(row) { return row.value; }));
      ctx.strokeStyle = '#dce4dd';
      ctx.fillStyle = '#60706a';
      ctx.font = '11px system-ui';
      ctx.beginPath();
      ctx.moveTo(pad.left, pad.top);
      ctx.lineTo(pad.left, pad.top + innerH);
      ctx.lineTo(pad.left + innerW, pad.top + innerH);
      ctx.stroke();
      if (!data.length) { ctx.fillText('Sin gastos para este filtro', pad.left + 10, pad.top + 24); return; }
      const groupW = innerW / data.length;
      data.forEach(function(row, index) {
        const barW = Math.max(8, groupW - 8);
        const value = row.value;
        const barH = (value / max) * innerH;
        const x = pad.left + index * groupW + (groupW - barW) / 2;
        const y = pad.top + innerH - barH;
        ctx.fillStyle = '#b34545';
        ctx.fillRect(x, y, barW, barH);
        ctx.fillStyle = '#60706a';
        const dayLabel = row.label.slice(0, 5);
        ctx.fillText(dayLabel, x, pad.top + innerH + 14);
        ctx.fillStyle = '#17211d';
        ctx.fillText(centsToMoney(value), x, Math.max(14, y - 6));
      });
    }
    function drawHorizontalBars(ctx, width, height, data) {
      const pad = {left: 130, right: 28, top: 18, bottom: 18};
      const innerW = width - pad.left - pad.right;
      const rowH = Math.max(24, (height - pad.top - pad.bottom) / Math.max(1, data.length));
      const max = Math.max(1, ...data.map(function(row) { return row.value; }));
      ctx.font = '12px system-ui';
      if (!data.length) { ctx.fillStyle = '#60706a'; ctx.fillText('Sin egresos', 12, 28); return; }
      data.forEach(function(row, index) {
        const y = pad.top + index * rowH;
        const barW = (row.value / max) * innerW;
        ctx.fillStyle = '#60706a';
        ctx.fillText(row.key, 10, y + 16);
        ctx.fillStyle = '#2c6387';
        ctx.fillRect(pad.left, y + 3, barW, Math.max(12, rowH - 10));
        ctx.fillStyle = '#17211d';
        ctx.fillText(centsToMoney(row.value), pad.left + barW + 6, y + 16);
      });
    }
    const RECEIPT_STATUS = {
      nuevo: {label: 'Pendiente de revisar', tone: 'warn'},
      pending: {label: 'Pendiente de revisar', tone: 'warn'},
      voice_pending: {label: 'Audio por transcribir', tone: 'warn'},
      dudoso: {label: 'Necesita revisión manual', tone: 'warn'},
      processed: {label: 'Procesado', tone: 'ok'},
      duplicado: {label: 'Duplicado', tone: 'neutral'},
      missing: {label: 'Archivo no encontrado', tone: 'danger'}
    };
    function receiptStatus(status) {
      return RECEIPT_STATUS[status] || {label: status || 'Desconocido', tone: 'neutral'};
    }
    function renderReceipts() {
      const list = document.getElementById('receiptList');
      if (!receipts.length) {
        list.innerHTML = '<div class="muted">Aún no hay archivos recibidos.</div>';
        return;
      }
      list.innerHTML = receipts.slice().reverse().map(function(row) {
        const state = receiptStatus(row.status);
        const link = row.url ? '<a href="' + row.url + '">Abrir archivo</a>' : '<span class="muted">Sin enlace</span>';
        const path = row.path
          ? '<details class="receipt-path"><summary>Ver ruta</summary><code>' + escapeHtml(row.path) + '</code></details>'
          : '';
        return '<div class="receipt-item">' +
          '<div class="receipt-head">' +
            '<span class="status-badge tone-' + state.tone + '">' + escapeHtml(state.label) + '</span>' +
            '<span class="muted">#' + row.id + ' · ' + escapeHtml(row.user) + ' · ' + escapeHtml(row.date) + '</span>' +
          '</div>' +
          (row.caption ? '<div>' + escapeHtml(row.caption) + '</div>' : '') +
          (row.reviewNotes ? '<div class="muted">' + escapeHtml(row.reviewNotes) + '</div>' : '') +
          '<div>' + link + '</div>' +
          path +
        '</div>';
      }).join('');
    }
    function render() {
      const rows = filteredTransactions();
      renderSummary(rows);
      renderAlerts(rows);
      renderIncomeList(rows);
      renderTable(rows);
      drawDailyExpenseChart(rows);
      drawCategoryChart(rows);
      renderReceipts();
    }
    Object.values(filters).forEach(function(control) { control.addEventListener('input', render); });
    Object.values(movementFilters).forEach(function(control) { control.addEventListener('input', render); });
    document.getElementById('resetFilters').addEventListener('click', function() {
      Object.values(filters).forEach(function(control) { control.value = ''; });
      movementFilters.category.value = '';
      movementFilters.type.value = '';
      movementFilters.store.value = '';
      movementFilters.user.value = '';
      movementFilters.amountSort.value = 'date_desc';
      render();
    });
    document.getElementById('transactionsBody').addEventListener('click', function(event) {
      const button = event.target.closest('[data-edit-id]');
      if (!button) return;
      const row = getTransactionById(button.getAttribute('data-edit-id'));
      if (row) openEditModal(row);
    });
    document.getElementById('cancelEdit').addEventListener('click', closeEditModal);
    document.getElementById('editModal').addEventListener('click', function(event) {
      if (event.target.id === 'editModal') closeEditModal();
    });
    document.getElementById('editForm').addEventListener('submit', submitEdit);
    document.querySelectorAll('.tab-button').forEach(function(button) {
      button.addEventListener('click', function() {
        showTab(button.getAttribute('data-tab') || 'dashboard');
      });
    });
    projectionMonth.addEventListener('input', renderProjection);
    analyticsMonth.addEventListener('input', renderAnalytics);
    document.getElementById('addProjection').addEventListener('click', openNewProjectionModal);
    document.getElementById('cashflowGrid').addEventListener('click', handleStatusClick);
    document.getElementById('projectionBody').addEventListener('click', function(event) {
      if (handleStatusClick(event)) return;
      const deleteButton = event.target.closest('[data-delete-projection-id]');
      if (deleteButton) {
        deleteProjectionItem(
          deleteButton.getAttribute('data-delete-projection-id'),
          deleteButton.getAttribute('data-projection-month')
        );
        return;
      }
      const button = event.target.closest('[data-projection-id]');
      if (!button) return;
      const row = getProjectionRow(
        button.getAttribute('data-projection-id'),
        button.getAttribute('data-projection-month')
      );
      if (row) openProjectionModal(row);
    });
    document.getElementById('cancelProjectionEdit').addEventListener('click', closeProjectionModal);
    document.getElementById('projectionModal').addEventListener('click', function(event) {
      if (event.target.id === 'projectionModal') closeProjectionModal();
    });
    document.getElementById('projectionForm').addEventListener('submit', submitProjectionEdit);
    document.getElementById('toggleFilters').addEventListener('click', function() {
      const panel = document.getElementById('moreFilters');
      const button = document.getElementById('toggleFilters');
      const open = panel.hidden;
      panel.hidden = !open;
      button.setAttribute('aria-expanded', String(open));
      button.textContent = open ? 'Menos filtros' : 'Más filtros';
    });
    function setupEditableUi() {
      // En modo lectura no mostramos controles de edición que no harían nada.
      const head = document.getElementById('transactionsHead');
      if (editable && head) {
        const th = document.createElement('th');
        th.textContent = 'Acciones';
        head.appendChild(th);
      }
      const addButton = document.getElementById('addProjection');
      if (addButton && !editable) addButton.hidden = true;
      const runtimeRefresh = document.getElementById('runtimeRefresh');
      if (runtimeRefresh) runtimeRefresh.addEventListener('click', refreshRuntimeStatus);
    }
    function setRuntimeItem(id, label, state) {
      const item = document.getElementById(id);
      if (!item) return;
      const dot = item.querySelector('.runtime-dot');
      const text = item.querySelector('.runtime-text');
      dot.className = 'runtime-dot' + (state === true ? ' online' : state === false ? ' offline' : '');
      text.textContent = label;
    }
    async function refreshRuntimeStatus() {
      if (!editable) return;
      try {
        const response = await fetch('/api/status', { cache: 'no-store' });
        if (!response.ok) throw new Error('Estado no disponible');
        const status = await response.json();
        setRuntimeItem(
          'runtimeBot',
          status.bot.running === true ? 'Bot conectado' : status.bot.running === false ? 'Bot detenido' : 'Bot no supervisado',
          status.bot.running
        );
        setRuntimeItem('runtimeDashboard', 'Panel conectado', true);
        const pending = document.getElementById('runtimePending');
        if (pending) pending.textContent = 'Pendientes: ' + status.pendingCount;
        const updated = document.getElementById('runtimeUpdated');
        const last = status.lastTransactionAt || status.lastReceiptAt;
        if (updated) updated.textContent = last ? 'Ultima actividad: ' + new Date(last).toLocaleString('es-ES') : 'Sin actividad registrada';
      } catch (error) {
        setRuntimeItem('runtimeBot', 'Estado no disponible', false);
        setRuntimeItem('runtimeDashboard', 'Panel sin respuesta', false);
      }
    }
    window.addEventListener('resize', function() {
      render();
      if (!document.getElementById('projectionPanel').hidden) renderProjection();
    });
    setupEditableUi();
    refreshRuntimeStatus();
    if (editable) window.setInterval(refreshRuntimeStatus, 5000);
    initFilters();
    initEditForm();
    initProjectionMonths();
    initProjectionForm();
    render();
    renderProjection();
    renderAnalytics();
  </script>
</body>
</html>
"""
    )
    return template.safe_substitute(
        title=escape("Reporte de finanzas"),
        refresh_meta="",
        generated_at=escape(generated_at),
        edit_hint=(
            '<span class="mode-badge edit">✎ Modo edición</span>'
            '<p class="mode-hint">Pulsa Editar en cualquier movimiento o proyección para corregirlo.</p>'
            if editable
            else '<span class="mode-badge read">Solo lectura</span>'
            '<p class="mode-hint">Este archivo es estático. Para editar, abre el panel local.</p>'
            '<div class="mode-actions">'
            '<a class="primary" href="http://127.0.0.1:8765/" target="_blank" rel="noreferrer">Abrir modo edición</a>'
            '</div>'
        ),
        runtime_status=(
            '<section class="runtime-status" id="runtimeStatus" aria-label="Estado de la aplicacion">'
            '<strong>Estado de Finanzas</strong>'
            '<span class="runtime-item" id="runtimeBot"><i class="runtime-dot"></i><span class="runtime-text">Comprobando bot...</span></span>'
            '<span class="runtime-item" id="runtimeDashboard"><i class="runtime-dot"></i><span class="runtime-text">Comprobando panel...</span></span>'
            '<span class="runtime-item" id="runtimePending">Pendientes: --</span>'
            '<span class="runtime-item" id="runtimeUpdated">Ultima actividad: --</span>'
            '<button class="secondary runtime-refresh" id="runtimeRefresh" type="button">Actualizar estado</button>'
            '</section>'
            if editable
            else ""
        ),
        db_path=escape(db_path),
        transactions_json=json.dumps(transactions, ensure_ascii=False),
        receipts_json=json.dumps(receipts, ensure_ascii=False),
        projections_json=json.dumps(projections, ensure_ascii=False),
        editable_json=json.dumps(editable),
        valid_categories_json=json.dumps(valid_categories, ensure_ascii=False),
    )
