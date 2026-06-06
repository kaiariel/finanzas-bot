from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


AMOUNT_RE = re.compile(
    r"(?<![\w])(?P<sign>[+-])?\s*(?P<amount>(?:\d{1,3}(?:[.,]\d{3})+|\d+)(?:[.,]\d{1,2})?)\s*(?P<currency>euros?|euro|eur|ero|€|usd|\$)?",
    re.IGNORECASE,
)

VALID_CATEGORIES = (
    "Alimentación",
    "Hogar",
    "Suministros",
    "Alquiler",
    "Izhan",
    "Salud & Cuidado",
    "Ropa",
    "Educación",
    "Deudas",
    "Ayuda familiar",
    "Suscripciones",
    "Transporte",
    "Ocio",
    "Ahorro",
    "Ingresos laborales",
    "Ingresos clientes",
    "Trabajos extra",
)

FIXED_CATEGORIES = {"Alquiler", "Deudas", "Ayuda familiar", "Suscripciones"}
INCOME_CATEGORIES = {"Ingresos laborales", "Ingresos clientes", "Trabajos extra"}
DEFAULT_EXPENSE_CATEGORY = "Ocio"
DEFAULT_INCOME_CATEGORY = "Trabajos extra"
CLIENT_REIMBURSABLE_EXPENSE_PHRASES = (
    "facebook ads",
    "google ads",
    "meta ads",
    "publicidad cliente",
    "publicidad clientes",
    "campana cliente",
    "campana clientes",
    "campanas cliente",
    "campanas clientes",
    "tiktok ads",
)

INCOME_WORDS = {
    "cobre",
    "cobro",
    "ingrese",
    "ingreso",
    "nomina",
    "paro",
    "recibi",
    "sueldo",
    "subsidio",
    "venta",
}

EXPENSE_WORDS = {
    "compra",
    "compre",
    "gaste",
    "gasto",
    "pague",
    "pago",
    "recibo",
    "ticket",
}

CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Alquiler", ("alquiler", "renta", "pago mensual vivienda", "vivienda")),
    (
        "Deudas",
        (
            "deuda",
            "deudas",
            "prestamo",
            "prestamos",
            "caixabank payments consumer",
            "financie",
            "jukyshop",
            "payments consumer",
            "financiacion",
            "cuota",
            "plazo",
            "samsung",
            "s24",
            "servicios financieros carrefour",
            "tarjeta carrefour",
        ),
    ),
    (
        "Ayuda familiar",
        ("paraguay", "ayuda familiar", "ayuda familia", "envio dinero", "remesa"),
    ),
    (
        "Suscripciones",
        (
            "netflix",
            "spotify",
            "claude",
            "chatgpt",
            "openai",
            "google cloud",
            "cloud apps",
            "apps",
            "anthropic",
            "antrophic",
            "creditos",
            "créditos",
            "capcut",
            "hosting",
            "suscripcion",
            "suscripciones",
            "app trabajo",
            "apps trabajo",
        ),
    ),
    (
        "Izhan",
        (
            "izhan",
            "panal",
            "panales",
            "potito",
            "potitos",
            "ropa bebe",
            "ropa de bebe",
            "juguete",
            "juguetes",
            "tarro",
            "tarros",
        ),
    ),
    (
        "Salud & Cuidado",
        (
            "farmacia",
            "medicamento",
            "medicamentos",
            "higiene personal",
            "champu",
            "desodorante",
            "dentista",
            "medico",
            "gel",
            "cuidado",
            "seguro salud",
            "seguro medico",
            "seguro médico",
            "peluqueria",
            "peluquería",
            "barberia",
            "barbería",
        ),
    ),
    ("Ropa", ("ropa", "calzado", "zapatos", "zapatillas", "zara", "primark", "shein")),
    (
        "Educación",
        ("curso", "cursos", "libro", "libros", "master", "materiales formativos", "formacion"),
    ),
    (
        "Suministros",
        ("agua", "electricidad", "gas", "luz", "endesa", "iberdrola", "naturgy"),
    ),
    (
        "Transporte",
        (
            "metro",
            "bus",
            "tren",
            "vuelo",
            "gasolina",
            "taxi",
            "uber",
            "cabify",
            "renfe",
            "combustible",
        ),
    ),
    ("Ahorro", ("ahorro", "alcancia", "fondo emergencia", "fondo de emergencia")),
    ("Ingresos laborales", ("nomina", "sueldo", "paro", "subsidio")),
    (
        "Ingresos clientes",
        (
            "cliente",
            "clientes",
            "marketing",
            "consultoria",
            "servicios cliente",
            "google ads",
            "meta ads",
            "facebook ads",
            "tiktok ads",
            "publicidad cliente",
            "publicidad clientes",
        ),
    ),
    ("Trabajos extra", ("freelance", "trabajo puntual", "puntual", "trabajos extra", "extra")),
    (
        "Alimentación",
        (
            "aldi",
            "carrefour",
            "comida",
            "fruta",
            "frutas",
            "verdura",
            "verduras",
            "lacteo",
            "lacteos",
            "carne",
            "carnes",
            "pescado",
            "lidl",
            "mas",
            "mercadona",
            "pan",
            "leche",
            "yogur",
            "yogures",
            "postre",
            "postres",
            "arroz",
            "pasta",
            "huevo",
            "huevos",
            "pollo",
            "aceite",
            "super",
            "supermercado",
        ),
    ),
    (
        "Hogar",
        (
            "limpieza",
            "menaje",
            "articulos hogar",
            "articulos del hogar",
            "deco",
            "hogar",
            "bolsa",
            "lejia",
            "detergente",
            "lavavajillas",
            "servilleta",
            "servilletas",
            "papel cocina",
            "papel higienico",
            "suavizante",
            "fregasuelos",
            "bayeta",
            "bolsas basura",
        ),
    ),
    (
        "Ocio",
        (
            "restaurante",
            "bar",
            "cine",
            "copas",
            "ocio",
            "cafe",
            "cena",
            "tapas",
            "apuesta",
            "apuestas",
        ),
    ),
)

STORE_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Lidl", ("lidl",)),
    ("Cash Fresh", ("cash fresh", "cashfresh")),
    ("Mercadona", ("mercadona",)),
    ("Amazon", ("amazon",)),
    ("Bizum", ("bizum",)),
    ("Transferencia", ("transferencia", "transferir", "transf")),
    ("Google Ads", ("google ads", "google adwords", "adwords")),
    ("Meta Ads", ("meta ads", "facebook ads", "instagram ads")),
    ("TikTok Ads", ("tiktok ads", "tik tok ads")),
    ("Aldi", ("aldi",)),
    ("Carrefour", ("carrefour",)),
    ("Dia", ("dia",)),
    ("Ikea", ("ikea",)),
    ("Leroy Merlin", ("leroy merlin",)),
    ("Zara", ("zara",)),
    ("H&M", ("h&m", "hm", "hennes")),
    ("Primark", ("primark",)),
    ("Shein", ("shein",)),
    ("Netflix", ("netflix",)),
    ("Spotify", ("spotify",)),
    ("Claude", ("claude",)),
    ("ChatGPT", ("chatgpt",)),
    ("OpenAI", ("openai",)),
    ("Anthropic", ("anthropic", "antrophic")),
    ("Capcut", ("capcut",)),
    ("Google Cloud", ("google cloud", "cloud apps")),
    ("Endesa", ("endesa",)),
    ("Iberdrola", ("iberdrola",)),
    ("Naturgy", ("naturgy",)),
    ("Repsol", ("repsol",)),
    ("Samsung", ("samsung",)),
    ("Uber", ("uber",)),
    ("Cabify", ("cabify",)),
    ("Renfe", ("renfe",)),
    ("Ryanair", ("ryanair",)),
    ("PayPal", ("paypal",)),
    ("Revolut", ("revolut",)),
    ("BBVA", ("bbva",)),
    ("CaixaBank", ("caixabank", "la caixa")),
    ("Santander", ("santander",)),
)

GROCERY_STORES = {"Aldi", "Carrefour", "Cash Fresh", "Dia", "Lidl", "MAS", "Mercadona"}

CURRENCY_ALIASES = {
    "$": "USD",
    "usd": "USD",
    "€": "EUR",
    "eur": "EUR",
    "ero": "EUR",
    "euro": "EUR",
    "euros": "EUR",
}


@dataclass(frozen=True)
class ParsedTransaction:
    kind: str
    amount_cents: int
    currency: str
    category: str
    note: str
    source_text: str
    store: str = ""
    is_fixed: bool = False
    needs_clarification: bool = False

    @property
    def amount(self) -> Decimal:
        return Decimal(self.amount_cents) / Decimal("100")


def normalize_text(text: str) -> str:
    lowered = text.strip().lower()
    normalized = unicodedata.normalize("NFKD", lowered)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _contains_keyword(normalized_text: str, keyword: str) -> bool:
    normalized_keyword = normalize_text(keyword)
    if " " in normalized_keyword or "&" in normalized_keyword:
        return normalized_keyword in normalized_text
    return bool(re.search(rf"\b{re.escape(normalized_keyword)}\b", normalized_text))


def amount_to_cents(raw_amount: str) -> int:
    cleaned = raw_amount.replace(" ", "")
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")

    try:
        amount = Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"Importe no valido: {raw_amount}") from exc

    cents = (amount * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(cents)


def _is_installment_number(text: str, match: re.Match[str]) -> bool:
    try:
        value = int(match.group("amount"))
    except ValueError:
        return False

    if value > 36:
        return False

    before = text[max(0, match.start() - 8) : match.start()].lower()
    after = text[match.end() : match.end() + 8].lower()
    return bool(re.search(r"\bde\s*$", before) or re.search(r"^\s*de\s+\d{1,2}\b", after))


def _amount_match_score(text: str, match: re.Match[str]) -> int:
    if _is_installment_number(text, match):
        return -1000

    raw_amount = match.group("amount")
    score = 0
    if match.group("currency"):
        score += 80
    if "," in raw_amount or "." in raw_amount:
        score += 40
    if match.group("sign"):
        score += 20

    context = normalize_text(text[max(0, match.start() - 24) : match.start()])
    if any(word in context for word in ("total", "importe", "descont", "cobr", "pago", "cuota")):
        score += 20

    return score


def find_amount_match(text: str) -> re.Match[str] | None:
    scored = [
        (_amount_match_score(text, match), match.start(), match)
        for match in AMOUNT_RE.finditer(text)
    ]
    valid = [item for item in scored if item[0] > -1000]
    if not valid:
        return None
    return max(valid, key=lambda item: (item[0], -item[1]))[2]


def infer_store(text: str) -> str:
    if re.search(r"\bMAS\b", text):
        return "MAS"

    normalized = normalize_text(text)
    if "super mas" in normalized or "supermas" in normalized:
        return "MAS"

    for canonical, aliases in STORE_ALIASES:
        if any(_contains_keyword(normalized, alias) for alias in aliases):
            return canonical
    return ""


def infer_category_from_keywords(text: str, store: str) -> str | None:
    normalized = normalize_text(text)
    if _contains_keyword(normalized, "apuesta") or _contains_keyword(normalized, "apuestas"):
        return "Ocio"

    for category, keywords in CATEGORY_KEYWORDS:
        if any(_contains_keyword(normalized, keyword) for keyword in keywords):
            return category

    if store in GROCERY_STORES:
        return "Alimentación"
    return None


def infer_kind(text: str, sign: str | None, category: str | None) -> str:
    normalized = normalize_text(text)
    words = set(re.findall(r"\w+", normalized))

    if sign == "+":
        return "income"
    if sign == "-":
        return "expense"
    if category == "Ingresos clientes" and is_client_reimbursable_expense(normalized):
        return "expense"
    if category and category not in INCOME_CATEGORIES and "cobro" in words:
        return "expense"
    if words & INCOME_WORDS:
        return "income"
    if words & EXPENSE_WORDS:
        return "expense"
    if category in INCOME_CATEGORIES:
        return "income"
    return "expense"


def infer_is_fixed(text: str, category: str) -> bool:
    normalized = normalize_text(text)
    if _contains_keyword(normalized, "creditos") or _contains_keyword(normalized, "créditos"):
        return False
    if _contains_keyword(normalized, "seguro salud"):
        return True
    if _contains_keyword(normalized, "musica de izhan") or _contains_keyword(
        normalized, "música de izhan"
    ):
        return True
    return category in FIXED_CATEGORIES


def is_client_reimbursable_expense(normalized_text: str) -> bool:
    if any(phrase in normalized_text for phrase in CLIENT_REIMBURSABLE_EXPENSE_PHRASES):
        return True
    has_ads = bool(re.search(r"\bads?\b", normalized_text))
    has_client_context = any(
        word in normalized_text
        for word in ("cliente", "clientes", "marketing", "campana", "campanas", "publicidad")
    )
    return has_ads and has_client_context


def clean_note(text: str, match: re.Match[str]) -> str:
    before = text[: match.start()].strip(" -:;,.")
    after = text[match.end() :].strip(" -:;,.")
    note = " ".join(part for part in (before, after) if part).strip()
    note = re.sub(
        r"\b(hoy|ayer|gasto|gaste|gasté|pago|pague|pagué|compra|compre|compré|ingreso|ingrese|ingresé|cobro|cobre|cobré|recibi|recibí|recibo|descontaron|descontado|cobraron|cobrado)\b\s*",
        "",
        note,
        flags=re.IGNORECASE,
    ).strip(" -:;,.")
    note = re.sub(r"^(en|a|de)\s+(el|la|los|las)?\s*", "", note, flags=re.IGNORECASE)
    return note or text.strip()


def parse_transaction(text: str) -> ParsedTransaction | None:
    match = find_amount_match(text)
    if not match:
        return None

    amount_cents = amount_to_cents(match.group("amount"))
    currency_raw = (match.group("currency") or "EUR").lower()
    currency = CURRENCY_ALIASES.get(currency_raw, currency_raw.upper())
    store = infer_store(text)
    detected_category = infer_category_from_keywords(text, store)
    kind = infer_kind(text, match.group("sign"), detected_category)
    category = detected_category or (
        DEFAULT_INCOME_CATEGORY if kind == "income" else DEFAULT_EXPENSE_CATEGORY
    )
    note = clean_note(text, match)
    needs_clarification = store in {"Bizum", "Transferencia"} and detected_category is None

    return ParsedTransaction(
        kind=kind,
        amount_cents=amount_cents,
        currency=currency,
        category=category,
        note=note,
        source_text=text.strip(),
        store=store,
        is_fixed=infer_is_fixed(text, category),
        needs_clarification=needs_clarification,
    )


def _split_inline_transactions(text: str) -> list[str]:
    cleaned = text.strip()
    if not cleaned:
        return []

    amount_count = sum(1 for match in AMOUNT_RE.finditer(cleaned) if not _is_installment_number(cleaned, match))
    if amount_count <= 1:
        return [cleaned]

    parts = [
        part.strip(" -;,.")
        for part in re.split(r";+|,(?!\d)", cleaned)
        if part.strip(" -;,.")
    ]
    return parts or [cleaned]


def parse_transactions(text: str) -> list[ParsedTransaction]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    candidates: list[str] = []
    for line in lines:
        candidates.extend(_split_inline_transactions(line))
    parsed = [transaction for line in candidates if (transaction := parse_transaction(line))]
    return mark_duplicates(parsed)


def mark_duplicates(transactions: list[ParsedTransaction]) -> list[ParsedTransaction]:
    seen: set[tuple[str, int, str]] = set()
    with_duplicates_marked: list[ParsedTransaction] = []
    for transaction in transactions:
        key = (
            normalize_text(transaction.note),
            transaction.amount_cents,
            transaction.kind,
        )
        if key in seen and "(revisar duplicado)" not in transaction.note:
            transaction = replace(
                transaction,
                note=f"{transaction.note} (revisar duplicado)",
            )
        seen.add(key)
        with_duplicates_marked.append(transaction)
    return with_duplicates_marked
