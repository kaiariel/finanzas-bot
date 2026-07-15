from finance_bot.bot import main
from finance_bot.config import Settings
from finance_bot.process_lock import AlreadyRunningError, SingleInstance


if __name__ == "__main__":
    settings = Settings.from_env()
    try:
        with SingleInstance(
            settings.data_dir / "telegram_bot.lock",
            "El bot de Telegram ya esta iniciado.",
        ):
            main()
    except AlreadyRunningError as exc:
        raise SystemExit(str(exc)) from exc
