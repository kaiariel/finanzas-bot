from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finance_bot.config import Settings
from finance_bot.process_lock import AlreadyRunningError, SingleInstance


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class ManagedProcess:
    name: str
    process: subprocess.Popen
    log_handle: object

    @property
    def running(self) -> bool:
        return self.process.poll() is None

    def close_log(self) -> None:
        close = getattr(self.log_handle, "close", None)
        if close:
            close()


def _port_is_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False


def _start_process(name: str, command: list[str], log_path: Path) -> ManagedProcess:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("a", encoding="utf-8")
    log_handle.write(f"\n[{datetime.now().isoformat(timespec='seconds')}] Inicio de {name}\n")
    log_handle.flush()
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )
    return ManagedProcess(name=name, process=process, log_handle=log_handle)


def _write_runtime_status(
    path: Path,
    *,
    started_at: str,
    bot: ManagedProcess | None,
    dashboard: ManagedProcess | None,
) -> None:
    payload = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "started_at": started_at,
        "supervisor_pid": os.getpid(),
        "bot_running": bool(bot and bot.running),
        "bot_pid": bot.process.pid if bot else None,
        "bot_exit_code": bot.process.poll() if bot else None,
        "dashboard_running": bool(dashboard and dashboard.running),
        "dashboard_pid": dashboard.process.pid if dashboard else None,
        "dashboard_exit_code": dashboard.process.poll() if dashboard else None,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _wait_for_dashboard(url: str, dashboard: ManagedProcess, timeout: float = 12.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not dashboard.running:
            return False
        try:
            with urlopen(url + "/api/status", timeout=0.5) as response:
                if response.status == 200:
                    return True
        except (OSError, URLError):
            time.sleep(0.2)
    return False


def _stop_process(process: ManagedProcess | None) -> None:
    if process is None:
        return
    try:
        if process.running:
            process.process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(
                ["taskkill.exe", "/PID", str(process.process.pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            process.process.terminate()
            try:
                process.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.process.kill()
        try:
            process.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pass
    finally:
        process.close_log()


def main() -> int:
    parser = argparse.ArgumentParser(description="Inicia el bot y el panel de finanzas")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--dashboard-only", action="store_true")
    args = parser.parse_args()

    settings = Settings.from_env()
    settings.ensure_core_dirs()
    lock_path = settings.data_dir / "finance_app.lock"
    status_path = settings.data_dir / "runtime_status.json"
    logs_dir = settings.data_dir / "logs"
    url = f"http://{args.host}:{args.port}"
    started_at = datetime.now(timezone.utc).isoformat()
    bot: ManagedProcess | None = None
    dashboard: ManagedProcess | None = None

    if _port_is_open(args.host, args.port):
        print(f"El puerto {args.port} ya esta ocupado. Cierra el panel anterior y vuelve a intentar.")
        return 2

    try:
        with SingleInstance(lock_path, "La aplicacion de finanzas ya esta iniciada."):
            dashboard = _start_process(
                "panel",
                [
                    sys.executable,
                    "-B",
                    str(ROOT / "scripts" / "serve_dashboard.py"),
                    "--host",
                    args.host,
                    "--port",
                    str(args.port),
                ],
                logs_dir / "dashboard.log",
            )
            _write_runtime_status(
                status_path,
                started_at=started_at,
                bot=bot,
                dashboard=dashboard,
            )
            if not _wait_for_dashboard(url, dashboard):
                print(f"El panel no pudo iniciar. Revisa {logs_dir / 'dashboard.log'}")
                return 3

            if not args.dashboard_only:
                bot = _start_process(
                    "bot",
                    [sys.executable, "-B", str(ROOT / "run_bot.py")],
                    logs_dir / "bot.log",
                )

            _write_runtime_status(
                status_path,
                started_at=started_at,
                bot=bot,
                dashboard=dashboard,
            )
            if not args.no_browser:
                webbrowser.open(url)

            print("Finanzas iniciadas.")
            print(f"Panel: {url}")
            print("Bot Telegram: no iniciado (modo panel)" if args.dashboard_only else "Bot Telegram: iniciado")
            print(f"Logs: {logs_dir.resolve()}")
            print("Pulsa Ctrl+C para cerrar.")

            bot_warning_shown = False
            dashboard_warning_shown = False
            while True:
                _write_runtime_status(
                    status_path,
                    started_at=started_at,
                    bot=bot,
                    dashboard=dashboard,
                )
                if bot and not bot.running and not bot_warning_shown:
                    print(f"El bot se detuvo. Revisa {logs_dir / 'bot.log'}")
                    bot_warning_shown = True
                if dashboard and not dashboard.running and not dashboard_warning_shown:
                    print(f"El panel se detuvo. Revisa {logs_dir / 'dashboard.log'}")
                    dashboard_warning_shown = True
                if (bot is None or not bot.running) and (dashboard is None or not dashboard.running):
                    return 1
                time.sleep(1)
    except AlreadyRunningError as exc:
        print(exc)
        return 2
    except KeyboardInterrupt:
        print("\nCerrando Finanzas...")
        return 0
    finally:
        _stop_process(bot)
        _stop_process(dashboard)
        try:
            _write_runtime_status(
                status_path,
                started_at=started_at,
                bot=bot,
                dashboard=dashboard,
            )
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
