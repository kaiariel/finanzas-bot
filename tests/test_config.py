from finance_bot.config import _parse_bool


def test_parse_bool_accepts_accented_si() -> None:
    assert _parse_bool("sí") is True


def test_parse_bool_accepts_uppercase_accented_si() -> None:
    assert _parse_bool("SÍ") is True
