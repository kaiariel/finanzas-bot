from __future__ import annotations

import re
import sqlite3
import sys
import os
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pypdf import PdfReader

from finance_bot.config import Settings
from finance_bot.db import FinanceDatabase
from finance_bot.formatting import format_euro
from finance_bot.local_transcription import (
    LocalTranscriptionUnavailable,
    transcribe_voice_file,
)
from finance_bot.parser import ParsedTransaction, amount_to_cents, parse_transactions
from finance_bot.receipt_items import ParsedReceiptItems, parse_supermarket_receipt_items
from finance_bot.report import generate_report


EUR_AMOUNT_RE = re.compile(r"(?P<sign>-)?(?P<amount>\d+(?:[.,]\d{2}))\s*(?:EUR|€)", re.I)
DATE_RE = re.compile(r"(?P<day>\d{1,2})/(?P<month>\d{1,2})/(?P<year>\d{4})")
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp"}


@dataclass(frozen=True)
class ReviewTransaction:
    kind: str
    amount_cents: int
    category: str
    note: str
    store: str = ""
    is_fixed: bool = False
    source_text: str = ""
    currency: str = "EUR"


def _extract_pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _extract_image_text(path: Path) -> str:
    try:
        from PIL import Image
        import pytesseract
    except ImportError as exc:
        raise RuntimeError("OCR local no instalado. Ejecuta pip install -r requirements.txt") from exc

    tesseract_cmd = os.getenv("TESSERACT_CMD")
    if not tesseract_cmd:
        for candidate in (
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ):
            if Path(candidate).exists():
                tesseract_cmd = candidate
                break
    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    try:
        return pytesseract.image_to_string(Image.open(path), lang="spa+eng")
    except pytesseract.TesseractNotFoundError as exc:
        raise RuntimeError("Tesseract OCR no esta instalado en Windows") from exc


def _signed_amount_cents(text: str) -> int | None:
    matches = list(EUR_AMOUNT_RE.finditer(text))
    if not matches:
        return None

    match = matches[-1]
    cents = amount_to_cents(match.group("amount"))
    return -cents if match.group("sign") else cents


def _receipt_date(row, text: str = "") -> str:
    match = DATE_RE.search(text)
    if not match:
        return row["created_at"]

    day = int(match.group("day"))
    month = int(match.group("month"))
    year = int(match.group("year"))
    return f"{year:04d}-{month:02d}-{day:02d}T12:00:00+02:00"


def _set_receipt(
    settings: Settings,
    receipt_id: int,
    *,
    status: str,
    caption: str | None = None,
    note: str | None = None,
) -> None:
    with sqlite3.connect(settings.sqlite_db_path) as connection:
        connection.execute(
            """
            UPDATE receipts
            SET status = ?,
                caption = COALESCE(?, caption),
                review_notes = COALESCE(?, review_notes)
            WHERE id = ?
            """,
            (status, caption, note, receipt_id),
        )
        connection.commit()


def _parsed_to_review(parsed: ParsedTransaction) -> ReviewTransaction:
    return ReviewTransaction(
        kind=parsed.kind,
        amount_cents=parsed.amount_cents,
        currency=parsed.currency,
        category=parsed.category,
        note=parsed.note,
        store=parsed.store,
        is_fixed=parsed.is_fixed,
        source_text=parsed.source_text,
    )


def _duplicate_for(
    db: FinanceDatabase,
    row,
    transaction: ReviewTransaction,
    *,
    created_at: str | None = None,
):
    return db.find_possible_duplicate(
        kind=transaction.kind,
        amount_cents=transaction.amount_cents,
        category=transaction.category,
        store=transaction.store,
        created_at=created_at or row["created_at"],
    )


def _add_if_new(
    settings: Settings,
    db: FinanceDatabase,
    row,
    transaction: ReviewTransaction,
    *,
    created_at: str | None = None,
) -> tuple[bool, int | None]:
    created_at = created_at or row["created_at"]
    duplicate = _duplicate_for(db, row, transaction, created_at=created_at)
    if duplicate:
        _set_receipt(
            settings,
            row["id"],
            status="duplicado",
            note=f"Coincide con movimiento #{duplicate['id']}: {transaction.note}",
        )
        return False, int(duplicate["id"])

    transaction_id = db.add_manual_transaction(
        kind=transaction.kind,
        amount_cents=transaction.amount_cents,
        currency=transaction.currency,
        category=transaction.category,
        note=transaction.note,
        store=transaction.store,
        is_fixed=transaction.is_fixed,
        source_text=transaction.source_text,
        created_at=created_at,
        receipt_local_path=row["local_path"],
        telegram_message_id=row["telegram_message_id"],
        telegram_user_id=row["telegram_user_id"],
        telegram_username=row["telegram_username"],
        telegram_full_name=row["telegram_full_name"],
        receipt_id=row["id"],
    )
    return True, transaction_id


def _receipt_already_has_transactions(settings: Settings, receipt_id: int) -> bool:
    with sqlite3.connect(settings.sqlite_db_path) as connection:
        row = connection.execute(
            "SELECT 1 FROM transactions WHERE receipt_id = ? LIMIT 1",
            (receipt_id,),
        ).fetchone()
    return row is not None


def _review_supermarket_items(
    settings: Settings,
    db: FinanceDatabase,
    row,
    parsed_receipt: ParsedReceiptItems,
    *,
    source_text: str,
    label: str,
) -> str:
    if _receipt_already_has_transactions(settings, row["id"]):
        _set_receipt(
            settings,
            row["id"],
            status="duplicado",
            note="El ticket ya tiene movimientos enlazados",
        )
        return f"#{row['id']} {label} duplicado: ticket ya registrado"

    created = 0
    for item in parsed_receipt.items:
        db.add_manual_transaction(
            kind="expense",
            amount_cents=item.amount_cents,
            currency="EUR",
            category=item.category,
            note=item.description,
            store=item.store,
            is_fixed=False,
            source_text=source_text,
            created_at=_receipt_date(row, source_text),
            receipt_local_path=row["local_path"],
            telegram_message_id=row["telegram_message_id"],
            telegram_user_id=row["telegram_user_id"],
            telegram_username=row["telegram_username"],
            telegram_full_name=row["telegram_full_name"],
            receipt_id=row["id"],
        )
        created += 1

    note = f"Ticket por items: {created} fila(s)"
    if parsed_receipt.total_cents is not None:
        note += f"; total {format_euro(parsed_receipt.total_cents)}"
    if parsed_receipt.difference_cents:
        note += f"; diferencia {format_euro(parsed_receipt.difference_cents)}"

    _set_receipt(
        settings,
        row["id"],
        status="processed",
        caption=f"{parsed_receipt.store or 'Supermercado'}: {created} item(s)",
        note=note,
    )
    return f"#{row['id']} {label} procesado por items: {created} filas"


def _review_voice(settings: Settings, db: FinanceDatabase, row) -> str:
    path = Path(row["local_path"])
    if not settings.voice_transcription_enabled:
        return f"#{row['id']} voz pendiente: transcripcion local desactivada"
    if not path.exists():
        _set_receipt(settings, row["id"], status="missing", note="archivo no encontrado")
        return f"#{row['id']} marcado como missing: archivo no encontrado"

    try:
        transcription = transcribe_voice_file(path, settings)
    except LocalTranscriptionUnavailable as exc:
        return f"#{row['id']} voz pendiente: {exc}"

    parsed = parse_transactions(transcription)
    if not parsed or any(item.needs_clarification for item in parsed):
        _set_receipt(
            settings,
            row["id"],
            status="dudoso",
            caption=f"transcripcion sin registrar: {transcription}",
            note="La transcripcion no deja claro categoria o tipo",
        )
        return f"#{row['id']} voz dudosa: transcripcion ambigua"

    created = sum(1 for item in parsed if _add_if_new(settings, db, row, _parsed_to_review(item))[0])
    if created == 0:
        return f"#{row['id']} voz duplicada"

    _set_receipt(
        settings,
        row["id"],
        status="processed",
        caption=f"transcripcion: {transcription}",
        note=f"{created} movimiento(s) registrado(s)",
    )
    return f"#{row['id']} voz procesada ({created} nuevos)"


def _review_carrefour_pdf(settings: Settings, db: FinanceDatabase, row, text: str) -> str | None:
    normalized = text.lower()
    if "servicios financieros carrefour" not in normalized and "tarjeta carrefour" not in normalized:
        return None

    amount = _signed_amount_cents(text)
    if amount is None:
        return f"#{row['id']} PDF Carrefour pendiente: no encuentro importe"

    amount_cents = abs(amount)
    amount_text = format_euro(amount_cents)
    created, duplicate_id = _add_if_new(
        settings,
        db,
        row,
        ReviewTransaction(
            kind="expense",
            amount_cents=amount_cents,
            category="Deudas",
            note="Tarjeta Carrefour Junio",
            store="Carrefour",
            is_fixed=True,
            source_text=text,
        ),
        created_at=_receipt_date(row, text),
    )
    if not created:
        return f"#{row['id']} PDF duplicado: coincide con movimiento #{duplicate_id}"

    _set_receipt(
        settings,
        row["id"],
        status="processed",
        caption=f"Tarjeta Carrefour {amount_text}",
        note="Regla local: servicios financieros Carrefour",
    )
    return f"#{row['id']} PDF procesado: Tarjeta Carrefour {amount_text}"


def _review_samsung_pdf(settings: Settings, db: FinanceDatabase, row, text: str) -> str | None:
    normalized = text.lower()
    if "caixabank payments consumer" not in normalized and "samsung" not in normalized:
        return None

    amount = _signed_amount_cents(text)
    if amount is None:
        return f"#{row['id']} PDF Samsung pendiente: no encuentro importe"

    amount_cents = abs(amount)
    created, duplicate_id = _add_if_new(
        settings,
        db,
        row,
        ReviewTransaction(
            kind="expense",
            amount_cents=amount_cents,
            category="Deudas",
            note="Samsung S24 cuota",
            store="Samsung",
            is_fixed=True,
            source_text=text,
        ),
        created_at=_receipt_date(row, text),
    )
    if not created:
        return f"#{row['id']} PDF duplicado: coincide con movimiento #{duplicate_id}"

    _set_receipt(
        settings,
        row["id"],
        status="processed",
        caption=f"Samsung {format_euro(amount_cents)}",
        note="Regla local: financiacion Samsung",
    )
    return f"#{row['id']} PDF procesado: Samsung {format_euro(amount_cents)}"


def _review_rent_water_pdf(settings: Settings, db: FinanceDatabase, row, text: str) -> str | None:
    caption = row["caption"] or ""
    haystack = f"{caption}\n{text}".lower()
    if "alquiler" not in haystack or "agua" not in haystack:
        return None

    total = _signed_amount_cents(text)
    water_match = re.search(r"agua\D{0,24}(\d+(?:[.,]\d{2}))", haystack)
    if total is None or not water_match:
        return None

    water = amount_to_cents(water_match.group(1))
    rent = abs(total) - water
    if rent <= 0:
        return None

    rent_tx = ReviewTransaction(
        kind="expense",
        amount_cents=rent,
        category="Alquiler",
        note="Alquiler Junio",
        is_fixed=True,
        source_text=f"{caption}\n{text}".strip(),
    )
    water_tx = ReviewTransaction(
        kind="expense",
        amount_cents=water,
        category="Suministros",
        note="Factura agua",
        is_fixed=False,
        source_text=f"{caption}\n{text}".strip(),
    )

    rent_created, rent_duplicate = _add_if_new(settings, db, row, rent_tx)
    water_created, water_duplicate = _add_if_new(settings, db, row, water_tx)
    if not rent_created and not water_created:
        _set_receipt(
            settings,
            row["id"],
            status="duplicado",
            note=f"Coincide con movimientos #{rent_duplicate} y #{water_duplicate}",
        )
        return f"#{row['id']} PDF duplicado: alquiler + agua ya registrados"

    _set_receipt(
        settings,
        row["id"],
        status="processed",
        caption=caption or "Alquiler y agua",
        note="Regla local: transferencia mixta alquiler + agua",
    )
    return f"#{row['id']} PDF procesado: alquiler + agua"


def _review_generic_text(settings: Settings, db: FinanceDatabase, row, text: str, label: str) -> str:
    parsed = [item for item in parse_transactions(text) if not item.needs_clarification]
    if not parsed:
        _set_receipt(
            settings,
            row["id"],
            status="dudoso",
            note=f"{label}: no hay importe/categoria clara",
        )
        return f"#{row['id']} {label} dudoso: necesita revision manual"

    created = sum(1 for item in parsed if _add_if_new(settings, db, row, _parsed_to_review(item))[0])
    if created == 0:
        return f"#{row['id']} {label} duplicado"

    _set_receipt(
        settings,
        row["id"],
        status="processed",
        caption=f"{label}: {created} movimiento(s)",
        note=f"{label}: lectura generica",
    )
    return f"#{row['id']} {label} procesado: {created} movimientos"


def _review_pdf(settings: Settings, db: FinanceDatabase, row) -> str:
    path = Path(row["local_path"])
    if not path.exists():
        _set_receipt(settings, row["id"], status="missing", note="archivo no encontrado")
        return f"#{row['id']} marcado como missing: archivo no encontrado"

    text = _extract_pdf_text(path)
    supermarket_items = parse_supermarket_receipt_items(text)
    if supermarket_items:
        return _review_supermarket_items(
            settings,
            db,
            row,
            supermarket_items,
            source_text=text,
            label="PDF",
        )

    for reviewer in (_review_carrefour_pdf, _review_samsung_pdf, _review_rent_water_pdf):
        result = reviewer(settings, db, row, text)
        if result:
            return result

    return _review_generic_text(settings, db, row, text, "PDF")


def _review_image(settings: Settings, db: FinanceDatabase, row) -> str:
    path = Path(row["local_path"])
    if not path.exists():
        _set_receipt(settings, row["id"], status="missing", note="archivo no encontrado")
        return f"#{row['id']} marcado como missing: archivo no encontrado"

    try:
        text = _extract_image_text(path)
    except RuntimeError as exc:
        _set_receipt(settings, row["id"], status="dudoso", note=str(exc))
        return f"#{row['id']} imagen dudosa: {exc}"

    supermarket_items = parse_supermarket_receipt_items(text)
    if supermarket_items:
        return _review_supermarket_items(
            settings,
            db,
            row,
            supermarket_items,
            source_text=text,
            label="imagen OCR",
        )

    return _review_generic_text(settings, db, row, text, "imagen OCR")


def main() -> None:
    settings = Settings.from_env()
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone)
    rows = db.list_pending_files()
    if not rows:
        print("No hay pendientes.")
        return

    results: list[str] = []
    for row in rows:
        path = Path(row["local_path"])
        suffix = path.suffix.lower()
        if row["status"] == "voice_pending" and suffix in {".ogg", ".oga", ".mp3", ".wav", ".m4a"}:
            results.append(_review_voice(settings, db, row))
        elif row["status"] in {"pending", "nuevo", "dudoso"} and suffix == ".pdf":
            results.append(_review_pdf(settings, db, row))
        elif row["status"] in {"pending", "nuevo", "dudoso"} and suffix in IMAGE_SUFFIXES:
            results.append(_review_image(settings, db, row))
        else:
            results.append(f"#{row['id']} pendiente: tipo no automatico")

    generate_report(settings)
    print("\n".join(results))


if __name__ == "__main__":
    main()
