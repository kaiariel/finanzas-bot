from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finance_bot.config import Settings
from finance_bot.db import FinanceDatabase
from finance_bot.parser import amount_to_cents


CURRENT_MONTH = "2026-06"


EXPENSES = [
    ("hosting sered", "37,00", "Pagado", "Fijo", "Suscripciones"),
    ("hostinger", "25,00", "", "Fijo", "Suscripciones"),
    ("Microsoft", "10,00", "", "Fijo", "Suscripciones"),
    ("Canva", "7,00", "", "Fijo", "Suscripciones"),
    ("Ionos", "1,30", "Pagado", "Fijo", "Suscripciones"),
    ("Freepik", "6,00", "", "Fijo", "Suscripciones"),
    ("Google", "20,00", "", "Fijo", "Suscripciones"),
    ("metricool", "18,00", "", "Fijo", "Suscripciones"),
    ("Capcut", "13,00", "", "Fijo", "Suscripciones"),
    ("adobe", "20,00", "", "Fijo", "Suscripciones"),
    ("chatgpt", "23,00", "", "Fijo", "Suscripciones"),
    ("Claude", "22,00", "", "Fijo", "Suscripciones"),
    ("telefono", "59,00", "Pagado", "1/12", "Deudas"),
    ("Seguro Santander", "22,00", "", "Fijo", "Hogar"),
    ("Contadora", "10,00", "", "Fijo", "Suscripciones"),
    ("Cu", "15,00", "Pagado", "26/60", "Deudas"),
    ("Cuota camara", "135,00", "", "6/12", "Deudas"),
    ("Musica Izhan", "25,00", "", "Fijo", "Izhan"),
    ("icloud", "3,00", "", "Fijo", "Suscripciones"),
    ("Disney", "14,00", "", "Fijo", "Suscripciones"),
    ("Prime", "5,00", "", "Fijo", "Suscripciones"),
    ("Gimnasio", "50,00", "", "Fijo", "Salud & Cuidado"),
    ("Internet", "20,00", "", "Fijo", "Suministros"),
    ("Alquiler", "700,00", "", "Fijo", "Alquiler"),
    ("Agua", "25,00", "", "Fijo", "Suministros"),
    ("Electricidad", "70,00", "", "Fijo", "Suministros"),
    ("Hogar/Alimentacion", "500,00", "", "Fijo", "Alimentación"),
    ("Izhan hijo", "250,00", "", "Fijo", "Izhan"),
    ("Pago amazon silla", "36,00", "", "3/4", "Deudas"),
]


INCOMES = [
    ("Sueldo cocina Ariel", "1.180,00", "Fijo", "Ingresos laborales"),
    ("Sueldo Dahiana", "455,00", "Fijo", "Ingresos laborales"),
    ("Paro Dahiana", "555,00", "Fijo", "Ingresos laborales"),
    ("Ayuda Izhan", "100,00", "Fijo", "Ingresos laborales"),
    ("Marketing Gallito", "250,00", "Cliente", "Ingresos clientes"),
    ("Marketing comte", "300,00", "Cliente", "Ingresos clientes"),
    ("Marketing Tribuna", "250,00", "Cliente", "Ingresos clientes"),
    ("Oñoiru", "250,00", "Cliente", "Ingresos clientes"),
    ("Extra Dahiana", "300,00", "Variable", "Trabajos extra"),
]


def _installment(value: str) -> tuple[int | None, int | None]:
    if "/" not in value:
        return None, None
    current, total = value.split("/", 1)
    return int(current.strip()), int(total.strip())


def main() -> None:
    settings = Settings.from_env()
    db = FinanceDatabase(settings.sqlite_db_path, settings.timezone)

    count = 0
    for index, (name, amount, status, quota, category) in enumerate(EXPENSES, start=1):
        current, total = _installment(quota)
        template_id = db.upsert_projection_template(
            kind="expense",
            name=name,
            default_amount_cents=amount_to_cents(amount),
            category=category,
            group_name="Gastos y suscripciones",
            start_month=CURRENT_MONTH,
            installment_current=current,
            installment_total=total,
            sort_order=index,
        )
        if status.strip().lower() == "pagado" and not db.get_projection_occurrence(
            template_id, CURRENT_MONTH
        ):
            db.set_projection_occurrence(
                template_id=template_id,
                month=CURRENT_MONTH,
                amount_cents=amount_to_cents(amount),
                status="completed",
                note="Importado como pagado",
            )
        count += 1

    offset = len(EXPENSES)
    for index, (name, amount, income_type, category) in enumerate(INCOMES, start=1):
        db.upsert_projection_template(
            kind="income",
            name=name,
            default_amount_cents=amount_to_cents(amount),
            category=category,
            group_name=income_type,
            start_month=CURRENT_MONTH,
            sort_order=offset + index,
        )
        count += 1

    print(f"Proyecciones importadas: {count}")


if __name__ == "__main__":
    main()
