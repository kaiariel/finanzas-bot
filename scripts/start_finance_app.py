from __future__ import annotations

import argparse
import ctypes
import json
import os
import socket
import subprocess
import sys
import time
import traceback
import webbrowser
from ctypes import wintypes
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finance_bot.config import Settings
from finance_bot.process_lock import AlreadyRunningError, SingleInstance
from finance_bot.storage import inspect_database, DatabaseUnavailableError


ROOT = Path(__file__).resolve().parents[1]
DETACHED_ENV_VAR = "FINANCE_APP_DETACHED"

# Codigos de salida
EXIT_OK = 0
EXIT_ALREADY_RUNNING = 2
EXIT_DASHBOARD_FAILED = 3
EXIT_NO_CONFIRMATION = 4
EXIT_UNEXPECTED = 5

# El estado se escribe en una carpeta sincronizada por OneDrive y lo lee el panel,
# asi que la sustitucion atomica puede chocar con otros procesos que lo tengan abierto.
STATUS_INTERVAL_SECONDS = 2.0
REPLACE_ATTEMPTS = 8
REPLACE_DELAY_SECONDS = 0.05
LAUNCH_TIMEOUT_SECONDS = 60.0

# Si el bot o el panel se caen (p. ej. sin red al encender la compu), el
# supervisor los relanza con espera creciente en lugar de dejarlos muertos.
RESTART_MIN_DELAY_SECONDS = 5.0
RESTART_MAX_DELAY_SECONDS = 300.0
STABLE_RUN_SECONDS = 300.0
LOG_ROTATE_BYTES = 5 * 1024 * 1024
AUTOSTART_FILENAME = "Finanzas.vbs"


@dataclass
class ManagedProcess:
    name: str
    process: subprocess.Popen
    log_handle: object
    started_monotonic: float = field(default_factory=time.monotonic)

    @property
    def running(self) -> bool:
        return self.process.poll() is None

    def close_log(self) -> None:
        close = getattr(self.log_handle, "close", None)
        if close:
            close()


@dataclass
class RestartPolicy:
    """Espera creciente entre reinicios; vuelve al minimo tras una ejecucion estable."""

    delay: float = RESTART_MIN_DELAY_SECONDS
    next_attempt: float | None = None

    def schedule(self, now: float, ran_for: float) -> float:
        if ran_for >= STABLE_RUN_SECONDS:
            self.delay = RESTART_MIN_DELAY_SECONDS
        wait = self.delay
        self.next_attempt = now + wait
        self.delay = min(self.delay * 2, RESTART_MAX_DELAY_SECONDS)
        return wait

    def due(self, now: float) -> bool:
        return self.next_attempt is not None and now >= self.next_attempt


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _port_is_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False


def _wait_for_port_free(host: str, port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if not _port_is_open(host, port):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.25)


def _process_is_running(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        synchronize = 0x00100000
        wait_timeout = 0x00000102
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == wait_timeout
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _process_image_name(pid: int) -> str:
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["tasklist.exe", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
                capture_output=True,
                text=True,
                check=False,
            )
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.startswith('"'):
                    return line.split('","')[0].strip('"').lower()
            return ""
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="],
            capture_output=True,
            text=True,
            check=False,
        )
        return result.stdout.strip().lower()
    except (OSError, ValueError):
        return ""


def _is_managed_pid(pid: object) -> bool:
    """Evita matar un PID reciclado por otro programa ajeno a Finanzas."""
    if not _process_is_running(pid):
        return False
    assert isinstance(pid, int)
    return "python" in _process_image_name(pid)


def _kill_pid(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    try:
        os.kill(pid, 15)
    except OSError:
        pass


def _read_json_file(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json_file(path: Path, payload: dict[str, object]) -> None:
    """Escribe JSON tolerando que otro proceso tenga el archivo abierto.

    En Windows replace() falla con PermissionError si el destino esta abierto
    (el panel lo lee cada pocos segundos y OneDrive lo escanea), asi que se
    reintenta y, como ultimo recurso, se escribe directamente.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(data, encoding="utf-8")
    last_error: OSError | None = None
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            temporary.replace(path)
            return
        except OSError as exc:
            last_error = exc
            time.sleep(REPLACE_DELAY_SECONDS * (attempt + 1))
    try:
        path.write_text(data, encoding="utf-8")
        temporary.unlink(missing_ok=True)
    except OSError:
        raise last_error if last_error else OSError(f"No se pudo escribir {path}")


def _rotate_log(log_path: Path, max_bytes: int = LOG_ROTATE_BYTES) -> None:
    """Conserva solo el registro actual y el anterior para que no crezcan sin limite."""
    try:
        if log_path.stat().st_size < max_bytes:
            return
        log_path.replace(log_path.with_name(log_path.name + ".1"))
    except OSError:
        pass


def _start_process(name: str, command: list[str], log_path: Path) -> ManagedProcess:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _rotate_log(log_path)
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
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )
    return ManagedProcess(name=name, process=process, log_handle=log_handle)


def _detached_command(argv: list[str]) -> list[str]:
    # El proceso padre conserva la consola y es quien abre el navegador cuando
    # el arranque se confirma, asi que el hijo nunca debe abrirlo por su cuenta.
    filtered_args = [arg for arg in argv if arg != "--detached"]
    if "--no-browser" not in filtered_args:
        filtered_args.append("--no-browser")
    return [sys.executable, "-B", str(Path(__file__).resolve()), *filtered_args]


def _should_relaunch_detached(detached: bool) -> bool:
    return detached and os.name == "nt" and os.environ.get(DETACHED_ENV_VAR) != "1"


def _launch_result_path(data_dir: Path) -> Path:
    return data_dir / "launch_result.json"


def _write_launch_result(path: Path, *, ok: bool, code: int, message: str) -> None:
    _write_json_file(
        path,
        {
            "ok": ok,
            "code": code,
            "message": message,
            "pid": os.getpid(),
            "updated_at": _now_iso(),
        },
    )


def _safe_write_launch_result(path: Path, *, ok: bool, code: int, message: str) -> None:
    try:
        _write_launch_result(path, ok=ok, code=code, message=message)
    except OSError as exc:
        print(f"Aviso: no se pudo registrar el resultado del arranque: {exc}")


def _tail(path: Path, lines: int = 12) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:]).strip()


def _runtime_status_payload(
    *,
    started_at: str,
    bot: ManagedProcess | None,
    dashboard: ManagedProcess | None,
) -> dict[str, object]:
    return {
        "updated_at": _now_iso(),
        "started_at": started_at,
        "supervisor_pid": os.getpid(),
        "bot_running": bool(bot and bot.running),
        "bot_pid": bot.process.pid if bot else None,
        "bot_exit_code": bot.process.poll() if bot else None,
        "dashboard_running": bool(dashboard and dashboard.running),
        "dashboard_pid": dashboard.process.pid if dashboard else None,
        "dashboard_exit_code": dashboard.process.poll() if dashboard else None,
    }


def _write_runtime_status(
    path: Path,
    *,
    started_at: str,
    bot: ManagedProcess | None,
    dashboard: ManagedProcess | None,
) -> None:
    _write_json_file(
        path,
        _runtime_status_payload(started_at=started_at, bot=bot, dashboard=dashboard),
    )


def _wait_for_dashboard(url: str, dashboard: ManagedProcess, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not dashboard.running:
            return False
        try:
            with urlopen(url + "/api/status", timeout=0.5) as response:
                if response.status == 200:
                    return True
        except (OSError, URLError):
            pass
        time.sleep(0.2)
    return False


def _stop_process(process: ManagedProcess | None) -> None:
    if process is None:
        return
    try:
        if not process.running:
            return
        process.process.terminate()
        try:
            process.process.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            pass
        if os.name == "nt":
            subprocess.run(
                ["taskkill.exe", "/PID", str(process.process.pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            process.process.kill()
        try:
            process.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    except OSError:
        pass
    finally:
        process.close_log()


def _cleanup_orphans(status_path: Path) -> list[str]:
    """Cierra panel o bot que quedaron vivos sin supervisor (cierre forzado previo)."""
    payload = _read_json_file(status_path)
    if not payload or _process_is_running(payload.get("supervisor_pid")):
        return []

    cleaned: list[str] = []
    for key, label in (("bot_pid", "bot"), ("dashboard_pid", "panel")):
        pid = payload.get(key)
        if not _is_managed_pid(pid):
            continue
        assert isinstance(pid, int)
        _kill_pid(pid)
        cleaned.append(f"{label} (PID {pid})")
    return cleaned


def _running_dashboard_matches(url: str, status_path: Path) -> bool:
    """Reutiliza solo el panel del supervisor registrado, no cualquier servidor."""
    runtime = _read_json_file(status_path)
    supervisor_pid = runtime.get("supervisor_pid")
    dashboard_pid = runtime.get("dashboard_pid")
    if not _process_is_running(supervisor_pid) or not _process_is_running(dashboard_pid):
        return False
    try:
        with urlopen(url + "/api/status", timeout=2.0) as response:
            if response.status != 200:
                return False
            status = json.loads(response.read(65536))
        return (
            isinstance(status, dict)
            and status.get("ok") is True
            and status.get("supervisor", {}).get("running") is True
            and status["supervisor"].get("pid") == supervisor_pid
            and status.get("dashboard", {}).get("running") is True
            and _dashboard_pid_matches(dashboard_pid, status["dashboard"].get("pid"))
        )
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def _windows_parent_pid(pid: int) -> int | None:
    # En Windows python.exe del venv puede redirigir al interprete real: Popen
    # conserva el PID del padre, pero /api/status devuelve el del hijo.
    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
        ]
    kernel = ctypes.windll.kernel32
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        return None
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        more = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            if entry.th32ProcessID == pid:
                return int(entry.th32ParentProcessID)
            more = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        return None
    finally:
        kernel.CloseHandle(snapshot)


def _dashboard_pid_matches(registered: int, actual: object) -> bool:
    if registered == actual:
        return True
    if os.name != "nt" or not isinstance(actual, int) or actual <= 0:
        return False
    try:
        return _windows_parent_pid(actual) == registered
    except OSError:
        return False


def _reuse_running_app(args: argparse.Namespace, settings: Settings) -> bool:
    url = f"http://{args.host}:{args.port}"
    if not _running_dashboard_matches(url, settings.data_dir / "runtime_status.json"):
        return False
    print(f"Finanzas ya esta lista. Panel: {url}")
    if not args.no_browser:
        webbrowser.open(url)
    return True


def _start_detached_once(args: argparse.Namespace, settings: Settings) -> int:
    # Dos clics simultaneos no deben lanzar dos supervisores ni compartir por
    # accidente el resultado de otro arranque.
    deadline = time.monotonic() + LAUNCH_TIMEOUT_SECONDS + 5
    while True:
        try:
            with SingleInstance(settings.data_dir / "finance_launch.lock"):
                if _reuse_running_app(args, settings):
                    return EXIT_OK
                return _run_detached(args, settings)
        except AlreadyRunningError:
            if time.monotonic() >= deadline:
                print("Finanzas sigue iniciandose. Revisa data/logs/launcher.log.")
                return EXIT_NO_CONFIRMATION
            time.sleep(0.3)


def _run_detached(args: argparse.Namespace, settings: Settings) -> int:
    """Lanza el supervisor en segundo plano y espera a que confirme el arranque."""
    data_dir = settings.data_dir
    logs_dir = data_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / "launcher.log"
    result_path = _launch_result_path(data_dir)
    try:
        result_path.unlink(missing_ok=True)
    except OSError:
        pass

    creationflags = 0
    for flag_name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP"):
        creationflags |= getattr(subprocess, flag_name, 0)

    env = os.environ.copy()
    env[DETACHED_ENV_VAR] = "1"
    env["PYTHONUNBUFFERED"] = "1"

    log_handle = log_path.open("a", encoding="utf-8")
    try:
        log_handle.write(
            f"\n[{datetime.now().isoformat(timespec='seconds')}] Inicio del supervisor\n"
        )
        log_handle.flush()
        child = subprocess.Popen(
            _detached_command(sys.argv[1:]),
            cwd=ROOT,
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
    finally:
        log_handle.close()

    print("Iniciando Finanzas...")
    url = f"http://{args.host}:{args.port}"

    def report(result: dict[str, object]) -> int:
        if result.get("ok"):
            print("Finanzas iniciada en segundo plano.")
            print(f"Panel: {url}")
            print('Usa "Cerrar Finanzas.cmd" para detenerla.')
            if not args.no_browser:
                webbrowser.open(url)
            return EXIT_OK
        print(str(result.get("message") or "Finanzas no pudo iniciar."))
        print(f"Detalles en {log_path}")
        code = result.get("code")
        return int(code) if isinstance(code, int) and code != 0 else EXIT_UNEXPECTED

    # El resultado se borro antes de lanzar, asi que cualquier archivo que
    # aparezca ahora es de este arranque. No se compara el PID porque el
    # python.exe del entorno virtual puede ser un redirector y ejecutar el
    # interprete real en otro proceso.
    deadline = time.monotonic() + LAUNCH_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        result = _read_json_file(result_path)
        if "ok" in result:
            return report(result)
        if child.poll() is not None:
            result = _read_json_file(result_path)
            if "ok" in result:
                return report(result)
            print("Finanzas se cerro durante el arranque.")
            detail = _tail(log_path)
            if detail:
                print("Ultimas lineas del registro:")
                print(detail)
            print(f"Detalles en {log_path}")
            return child.returncode or EXIT_UNEXPECTED
        time.sleep(0.3)

    print(f"Finanzas no confirmo el arranque en {int(LAUNCH_TIMEOUT_SECONDS)} segundos.")
    print(f"Revisa {log_path}")
    return EXIT_NO_CONFIRMATION


def _stop_running_app(args: argparse.Namespace, settings: Settings) -> int:
    status_path = settings.data_dir / "runtime_status.json"
    payload = _read_json_file(status_path)
    stopped: list[str] = []

    # Primero el supervisor: si siguiera vivo, relanzaria al bot y al panel.
    supervisor_pid = payload.get("supervisor_pid")
    if _is_managed_pid(supervisor_pid):
        assert isinstance(supervisor_pid, int)
        _kill_pid(supervisor_pid)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and _process_is_running(supervisor_pid):
            time.sleep(0.2)
        stopped.append(f"supervisor (PID {supervisor_pid})")

    for key, label in (("bot_pid", "bot"), ("dashboard_pid", "panel")):
        pid = payload.get(key)
        if not _is_managed_pid(pid):
            continue
        assert isinstance(pid, int)
        _kill_pid(pid)
        stopped.append(f"{label} (PID {pid})")

    if not stopped:
        print("Finanzas no estaba iniciada.")
    else:
        for item in stopped:
            print(f"Detenido: {item}")

    if not _wait_for_port_free(args.host, args.port, 5.0):
        print(f"Aviso: el puerto {args.port} sigue ocupado por otro programa.")
        return EXIT_ALREADY_RUNNING

    if stopped:
        try:
            _write_runtime_status(status_path, started_at=_now_iso(), bot=None, dashboard=None)
        except OSError:
            pass
        print("Finanzas se cerro.")
    return EXIT_OK


def _run_supervisor(args: argparse.Namespace, settings: Settings) -> int:
    lock_path = settings.data_dir / "finance_app.lock"
    status_path = settings.data_dir / "runtime_status.json"
    result_path = _launch_result_path(settings.data_dir)
    logs_dir = settings.data_dir / "logs"
    url = f"http://{args.host}:{args.port}"
    started_at = _now_iso()
    bot: ManagedProcess | None = None
    dashboard: ManagedProcess | None = None
    backup: ManagedProcess | None = None
    status_warning_shown = False

    def fail(code: int, message: str) -> int:
        print(message)
        _safe_write_launch_result(result_path, ok=False, code=code, message=message)
        return code

    def write_status() -> None:
        nonlocal status_warning_shown
        try:
            _write_runtime_status(
                status_path, started_at=started_at, bot=bot, dashboard=dashboard
            )
        except OSError as exc:
            # Nunca debe tumbar la aplicacion: el panel tolera un estado desactualizado.
            if not status_warning_shown:
                print(f"Aviso: no se pudo actualizar el estado ({exc}). Finanzas sigue activa.")
                status_warning_shown = True

    if _reuse_running_app(args, settings):
        _safe_write_launch_result(result_path, ok=True, code=EXIT_OK, message="Finanzas ya esta iniciada.")
        return EXIT_OK

    orphans = _cleanup_orphans(status_path)
    for orphan in orphans:
        print(f"Cerrado proceso huerfano de la sesion anterior: {orphan}")

    # Solo merece la pena esperar al puerto si se acaba de cerrar algo que lo tenia.
    if not _wait_for_port_free(args.host, args.port, 6.0 if orphans else 0.5):
        return fail(
            EXIT_ALREADY_RUNNING,
            f"El puerto {args.port} ya esta ocupado. Cierra Finanzas con "
            '"Cerrar Finanzas.cmd" y vuelve a intentarlo.',
        )

    dashboard_command = [
        sys.executable,
        "-B",
        str(ROOT / "scripts" / "serve_dashboard.py"),
        "--host",
        args.host,
        "--port",
        str(args.port),
    ]
    bot_command = [sys.executable, "-B", str(ROOT / "run_bot.py")]

    try:
        with SingleInstance(lock_path, "La aplicacion de finanzas ya esta iniciada."):
            dashboard = _start_process("panel", dashboard_command, logs_dir / "dashboard.log")
            write_status()
            if not _wait_for_dashboard(url, dashboard):
                return fail(
                    EXIT_DASHBOARD_FAILED,
                    f"El panel no pudo iniciar. Revisa {logs_dir / 'dashboard.log'}",
                )

            if not args.dashboard_only:
                bot = _start_process("bot", bot_command, logs_dir / "bot.log")

            write_status()
            _safe_write_launch_result(
                result_path, ok=True, code=EXIT_OK, message=f"Finanzas iniciada. Panel: {url}"
            )
            if not args.no_browser:
                webbrowser.open(url)

            print("Finanzas iniciadas.")
            print(f"Panel: {url}")
            print(
                "Bot Telegram: no iniciado (modo panel)"
                if args.dashboard_only
                else "Bot Telegram: iniciado"
            )
            print(f"Logs: {logs_dir.resolve()}")
            print("Pulsa Ctrl+C para cerrar.")

            bot_restarts = RestartPolicy()
            dashboard_restarts = RestartPolicy()
            next_backup = 0.0

            def keep_alive(
                current: ManagedProcess | None,
                policy: RestartPolicy,
                command: list[str],
                log_name: str,
            ) -> ManagedProcess | None:
                if current is None or current.running:
                    return current
                now = time.monotonic()
                if policy.next_attempt is None:
                    wait = policy.schedule(now, now - current.started_monotonic)
                    print(
                        f"{_now_iso()} {current.name} se detuvo (codigo "
                        f"{current.process.returncode}). Reintento en {wait:.0f}s. "
                        f"Revisa {logs_dir / log_name}"
                    )
                    return current
                if not policy.due(now):
                    return current
                current.close_log()
                policy.next_attempt = None
                print(f"{_now_iso()} Reiniciando {current.name}...")
                return _start_process(current.name, command, logs_dir / log_name)

            while True:
                if time.monotonic() >= next_backup and (backup is None or not backup.running):
                    if backup:
                        backup.close_log()
                    backup = _start_process("backup", [sys.executable, "-B", str(ROOT / "scripts" / "backup_finances.py"), "--if-due"], logs_dir / "backup.log")
                    next_backup = time.monotonic() + 3600
                bot = keep_alive(bot, bot_restarts, bot_command, "bot.log")
                dashboard = keep_alive(
                    dashboard, dashboard_restarts, dashboard_command, "dashboard.log"
                )
                write_status()
                time.sleep(STATUS_INTERVAL_SECONDS)
    except AlreadyRunningError as exc:
        return fail(EXIT_ALREADY_RUNNING, str(exc))
    except KeyboardInterrupt:
        print("\nCerrando Finanzas...")
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001 - el supervisor nunca debe morir en silencio
        traceback.print_exc()
        return fail(EXIT_UNEXPECTED, f"Error inesperado al iniciar Finanzas: {exc}")
    finally:
        _stop_process(bot)
        _stop_process(dashboard)
        _stop_process(backup)
        try:
            _write_runtime_status(
                status_path, started_at=started_at, bot=bot, dashboard=dashboard
            )
        except OSError:
            pass


def _startup_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise OSError("No se encontro APPDATA; el arranque automatico solo existe en Windows.")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def _autostart_script() -> str:
    # Usa el mismo acceso sin consola que el escritorio. No abre el navegador
    # al entrar en Windows; el supervisor mantiene Telegram y el panel activos.
    launcher = str(ROOT / "Iniciar Finanzas.vbs").replace('"', '""')
    return (
        "' Finanzas: inicio automatico administrado\r\n"
        'Option Explicit\r\nDim shell, launcher, command\r\n'
        'Set shell = CreateObject("WScript.Shell")\r\n'
        f'launcher = "{launcher}"\r\n'
        'command = Chr(34) & shell.ExpandEnvironmentStrings("%WINDIR%\\System32\\wscript.exe") & Chr(34) & '
        '" //nologo " & Chr(34) & launcher & Chr(34) & " --no-browser"\r\n'
        'shell.Run command, 0, False\r\n'
    )


def _remove_legacy_autostart() -> bool:
    legacy = _startup_dir() / "Finanzas.cmd"
    content = _tail(legacy, lines=20)
    if str(ROOT) not in content or "start_finance_app.py" not in content:
        return False
    legacy.unlink()
    return True


def _install_autostart() -> int:
    if not (ROOT / "Iniciar Finanzas.vbs").is_file():
        print("No se encontro Iniciar Finanzas.vbs. Revisa la instalacion.")
        return EXIT_UNEXPECTED
    target = _startup_dir() / AUTOSTART_FILENAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_autostart_script(), encoding="utf-16")
    _remove_legacy_autostart()
    print(f"Finanzas arrancara sola al iniciar sesion en Windows ({target}).")
    return EXIT_OK


def _remove_autostart() -> int:
    target = _startup_dir() / AUTOSTART_FILENAME
    removed = _remove_legacy_autostart()
    if target.exists():
        target.unlink()
        removed = True
    if removed:
        print("Arranque automatico desactivado.")
    else:
        print("El arranque automatico no estaba activado.")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inicia el bot y el panel de finanzas")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--dashboard-only", action="store_true")
    parser.add_argument(
        "--detached",
        action="store_true",
        help="Relanza la aplicacion en segundo plano y libera la consola actual.",
    )
    parser.add_argument(
        "--stop",
        action="store_true",
        help="Detiene la aplicacion que este iniciada y libera el puerto.",
    )
    parser.add_argument(
        "--install-autostart",
        action="store_true",
        help="Inicia Finanzas automaticamente al iniciar sesion en Windows.",
    )
    parser.add_argument(
        "--remove-autostart",
        action="store_true",
        help="Desactiva el inicio automatico.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.install_autostart:
        return _install_autostart()
    if args.remove_autostart:
        return _remove_autostart()

    # Toda la configuracion usa rutas relativas, asi que el directorio de trabajo
    # debe ser la raiz del proyecto aunque el acceso directo se lance desde otro sitio.
    os.chdir(ROOT)
    settings = Settings.from_env()
    settings.ensure_core_dirs()

    if args.stop:
        return _stop_running_app(args, settings)
    if _reuse_running_app(args, settings):
        return EXIT_OK
    try:
        inspect_database(settings.sqlite_db_path, check_integrity=True)
    except DatabaseUnavailableError as exc:
        print(str(exc))
        return EXIT_UNEXPECTED
    if _should_relaunch_detached(args.detached):
        return _start_detached_once(args, settings)
    return _run_supervisor(args, settings)


if __name__ == "__main__":
    raise SystemExit(main())
