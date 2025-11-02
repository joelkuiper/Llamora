import atexit
import logging
import os
import shlex
import signal
import socket
import subprocess
import tempfile
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import orjson

from llamora.settings import settings


def _to_plain_dict(data: Any) -> dict[str, Any]:
    if data is None:
        return {}
    if hasattr(data, "to_dict"):
        data = data.to_dict()
    if isinstance(data, Mapping):
        return dict(data)
    return {}


def _normalise_arg_keys(args: dict[str, Any]) -> dict[str, Any]:
    normalised: dict[str, Any] = {}
    for key, value in args.items():
        key_str = str(key).replace("-", "_").lower()
        normalised[key_str] = value
    return normalised


def _server_args_to_cli(args: dict[str, Any]) -> list[str]:
    cli_args: list[str] = []
    for k, v in args.items():
        if v is None or v is False:
            continue
        flag = f"--{k.replace('_', '-')}"
        if v is True:
            cli_args.append(flag)
        else:
            cli_args.extend([flag, str(v)])
    return cli_args


def _find_free_port() -> int:
    """Return an available TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LlamafileProcessManager:
    """Manage lifecycle of a llamafile subprocess."""

    def __init__(self, server_args: dict | None = None) -> None:
        self.logger = logging.getLogger(__name__)

        self.port = _find_free_port()
        self.restart_attempts = 0
        self.max_restarts = 3

        server_cfg = settings.LLM.server
        host = server_cfg.get("host")
        llamafile_path = server_cfg.get("llamafile_path")
        cfg_server_args = _normalise_arg_keys(
            _to_plain_dict(server_cfg.get("args", {}))
        )
        cfg_server_args.update(_normalise_arg_keys(_to_plain_dict(server_args)))

        self._ctx_size = cfg_server_args.get("ctx_size")

        self._state_file = Path(tempfile.gettempdir()) / "llamora_llm_state.json"

        if host:
            self.proc: subprocess.Popen[str] | None = None
            self.server_url = host
            self.cmd: list[str] | None = None
            self.logger.info("Using external llama server at %s", host)
        else:
            if not llamafile_path:
                raise ValueError(
                    "Configure settings.LLM.server.llamafile_path or set "
                    "LLAMORA_LLM__SERVER__LLAMAFILE_PATH"
                )

            self._cleanup_stale_process()

            command_args = {**cfg_server_args, "port": self.port}
            self.cmd = [
                "sh",
                llamafile_path,
                *_server_args_to_cli(command_args),
            ]

            self.server_url = f"http://127.0.0.1:{self.port}"

            self.proc = None
            self._launch_server()
            atexit.register(self.shutdown)
            self._orig_signals: dict[int, object] = {}
            for sig in (signal.SIGINT, signal.SIGTERM):
                self._orig_signals[sig] = signal.getsignal(sig)
                signal.signal(sig, self._handle_exit)

    @property
    def ctx_size(self) -> int | None:
        return self._ctx_size

    def base_url(self) -> str:
        return self.server_url

    def ensure_server_running(self) -> None:
        if getattr(self, "cmd", None) is None:
            if not self._is_server_healthy():
                raise RuntimeError("LLM service is unavailable")
            return

        if self.proc is None:
            raise RuntimeError("LLM service process not started")

        if self.proc.poll() is not None or not self._is_server_healthy():
            self._restart_server()

    def shutdown(self) -> None:
        self.logger.info("Shutting called")
        proc = getattr(self, "proc", None)
        if proc and proc.poll() is None:
            self.logger.info("Stopping llamafile server")
            terminated = False
            if hasattr(os, "killpg"):
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    terminated = True
                except ProcessLookupError:
                    pass
            if not terminated:
                proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover - unlikely
                self.logger.warning("Forcing llamafile server kill")
                if hasattr(os, "killpg"):
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            self.logger.info("Llamafile server stopped")
        self._clear_state()
        self.proc = None

    def _state_path(self) -> Path:
        return self._state_file

    def _read_state(self) -> dict[str, Any] | None:
        state_path = self._state_path()
        if not state_path.exists():
            return None
        try:
            data = orjson.loads(state_path.read_bytes())
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        try:
            state_path.unlink()
        except FileNotFoundError:
            pass
        return None

    def _write_state(self) -> None:
        proc = getattr(self, "proc", None)
        if not proc or proc.poll() is not None:
            return
        state = {
            "pid": proc.pid,
            "pgid": os.getpgid(proc.pid) if hasattr(os, "getpgid") else None,
            "port": self.port,
        }
        state_path = self._state_path()
        try:
            state_path.write_bytes(orjson.dumps(state))
        except Exception:
            self.logger.debug("Failed to persist llamafile state", exc_info=True)

    def _clear_state(self) -> None:
        state_path = self._state_path()
        try:
            state_path.unlink()
        except FileNotFoundError:
            pass

    def _process_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            # On some platforms (notably macOS) PIDs can be re-used by
            # processes that we don't own between runs. In that case the
            # permission error indicates we no longer control the process,
            # so treat it as non-existent for our cleanup logic.
            return False

    def _terminate_process(
        self, pid: int, pgid: int | None, *, force: bool = False
    ) -> None:
        sig = signal.SIGKILL if force else signal.SIGTERM
        try:
            if pgid is not None and hasattr(os, "killpg"):
                os.killpg(pgid, sig)
            else:
                os.kill(pid, sig)
        except ProcessLookupError:
            pass
        except PermissionError:
            self.logger.debug(
                "Permission denied when attempting to signal process %s (pgid %s)",
                pid,
                pgid,
                exc_info=True,
            )

    def _cleanup_stale_process(self) -> None:
        state = self._read_state()
        if not state:
            return
        pid = state.get("pid")
        pgid = state.get("pgid") or pid
        if not isinstance(pid, int):
            self._clear_state()
            return
        if self._process_alive(pid):
            self.logger.info(
                "Found existing llamafile process (pid %s), terminating before restart",
                pid,
            )
            self._terminate_process(pid, pgid, force=False)
            for _ in range(50):
                if not self._process_alive(pid):
                    break
                time.sleep(0.1)
            if self._process_alive(pid):
                self.logger.warning(
                    "Existing llamafile process %s did not exit, forcing kill",
                    pid,
                )
                self._terminate_process(pid, pgid, force=True)
        self._clear_state()

    def _wait_until_ready(self) -> None:
        for _ in range(100):
            try:
                resp = httpx.get(f"{self.server_url}/health", timeout=1.0)
                if resp.json().get("status") == "ok":
                    self.logger.info("Llamafile server responded with ok status")
                    return
            except Exception:
                pass
            time.sleep(0.1)
        raise RuntimeError("LLM service failed to start")

    def _log_stream(self, stream, level: int) -> None:
        for line in iter(stream.readline, ""):
            if line:
                self.logger.log(level, line.rstrip())

    def _launch_server(self) -> None:
        cmd = getattr(self, "cmd", None)
        if not cmd:
            return
        pretty_cmd = shlex.join(cmd)
        self.logger.info("Starting llamafile with: %s", pretty_cmd)
        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        if self.proc.stdout:
            threading.Thread(
                target=self._log_stream,
                args=(self.proc.stdout, logging.INFO),
                daemon=True,
            ).start()
        if self.proc.stderr:
            threading.Thread(
                target=self._log_stream,
                args=(self.proc.stderr, logging.INFO),
                daemon=True,
            ).start()
        self._wait_until_ready()
        self._write_state()
        self.restart_attempts = 0

    def _is_server_healthy(self) -> bool:
        try:
            resp = httpx.get(f"{self.server_url}/health", timeout=1.0)
            return resp.json().get("status") == "ok"
        except Exception:
            return False

    def _restart_server(self) -> None:
        if not getattr(self, "cmd", None):
            raise RuntimeError("Cannot restart external server")
        if self.restart_attempts >= self.max_restarts:
            raise RuntimeError("LLM service repeatedly crashed")
        self.restart_attempts += 1
        self.logger.warning(
            "Restarting llamafile server (attempt %d)", self.restart_attempts
        )
        self.shutdown()
        self._launch_server()

    def _handle_exit(self, signum, frame) -> None:  # pragma: no cover - signal handler
        try:
            self.shutdown()
        finally:
            handler = getattr(self, "_orig_signals", {}).get(signum, signal.SIG_DFL)
            if handler in (signal.SIG_IGN, None):
                return
            if handler is signal.SIG_DFL:
                signal.signal(signum, signal.SIG_DFL)
                os.kill(os.getpid(), signum)
            elif callable(handler):
                handler(signum, frame)

    def __del__(self):
        try:
            self.shutdown()
        except Exception:
            pass
