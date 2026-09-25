"""Double-click entry point for the bundled Windows application."""

import ctypes
import json
import logging
import os
import socket
import sys
import threading
import time
import traceback
import urllib.request
import uuid
import webbrowser
from pathlib import Path

import uvicorn

from backend.main import app
from backend.paths import data_dir, frontend_dist, images_dir, logs_dir

APP_NAME = "MoneyEngineV3"
HOST = "127.0.0.1"
PREFERRED_PORT = 8000


class InstanceLock:
    def __init__(self):
        self.handle = None
        self.file = None

    def acquire(self) -> bool:
        if os.name == "nt":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p)
            kernel32.CreateMutexW.restype = ctypes.c_void_p
            self.handle = kernel32.CreateMutexW(None, False, "Local\\MoneyEngineV3")
            if not self.handle:
                raise OSError(ctypes.get_last_error(), "프로그램 잠금을 만들지 못했습니다.")
            return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS
        import fcntl

        self.file = (data_dir() / "instance.lock").open("a+b")
        try:
            fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False

    def close(self):
        if self.handle:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
            kernel32.CloseHandle(self.handle)
            self.handle = None
        if self.file:
            self.file.close()
            self.file = None


def runtime_path() -> Path:
    return data_dir() / "runtime.json"


def existing_url() -> str | None:
    try:
        state = json.loads(runtime_path().read_text(encoding="utf-8"))
        port = state["port"]
        if state.get("app") != APP_NAME or not isinstance(port, int) or not 1 <= port <= 65535:
            return None
        url = f"http://{HOST}:{port}"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url + "/api/health", timeout=0.5) as response:
            health = json.load(response)
        if health.get("app") == APP_NAME and health.get("status") == "ok":
            return url
    except (OSError, ValueError, KeyError, TimeoutError):
        pass
    return None


def available_socket() -> tuple[socket.socket, int]:
    for port in (PREFERRED_PORT, 0):
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            server_socket.bind((HOST, port))
            server_socket.listen(128)
            return server_socket, server_socket.getsockname()[1]
        except OSError:
            server_socket.close()
    raise RuntimeError("사용 가능한 로컬 포트를 찾지 못했습니다.")


def show_error(message: str):
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(None, message, "Money Engine 실행 오류", 0x10)
    else:
        print(message, file=sys.stderr)


def configure_logging() -> Path:
    logs_dir().mkdir(parents=True, exist_ok=True)
    log_file = logs_dir() / "money-engine.log"
    logging.basicConfig(filename=log_file, level=logging.INFO, force=True,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return log_file


def open_when_ready(server: uvicorn.Server, port: int, token: str):
    for _ in range(300):
        if server.started:
            state = {"app": APP_NAME, "port": port, "pid": os.getpid(), "token": token}
            temp = runtime_path().with_suffix(".tmp")
            temp.write_text(json.dumps(state), encoding="utf-8")
            os.replace(temp, runtime_path())
            if os.environ.get("MONEY_ENGINE_DISABLE_BROWSER") != "1":
                webbrowser.open(f"http://{HOST}:{port}")
            return
        if server.should_exit:
            return
        time.sleep(0.1)


def run() -> None:
    data_dir().mkdir(parents=True, exist_ok=True)
    images_dir().mkdir(parents=True, exist_ok=True)
    configure_logging()
    if not (frontend_dist() / "index.html").is_file():
        raise RuntimeError("화면 파일이 없습니다. 배포 압축의 모든 파일을 함께 풀어주세요.")

    lock = InstanceLock()
    if not lock.acquire():
        try:
            for _ in range(50):
                url = existing_url()
                if url:
                    if os.environ.get("MONEY_ENGINE_DISABLE_BROWSER") != "1":
                        webbrowser.open(url)
                    return
                time.sleep(0.1)
            raise RuntimeError("프로그램이 이미 실행 중이지만 브라우저 주소를 확인하지 못했습니다.")
        finally:
            lock.close()

    token = uuid.uuid4().hex
    server_socket = None
    opener = None
    try:
        server_socket, port = available_socket()
        server = uvicorn.Server(uvicorn.Config(app, host=HOST, port=port, log_level="info",
                                               log_config=None))
        app.state.server = server
        opener = threading.Thread(target=open_when_ready, args=(server, port, token), daemon=True)
        opener.start()
        logging.info("Starting Money Engine at http://%s:%s", HOST, port)
        server.run(sockets=[server_socket])
        if not server.started:
            raise RuntimeError("로컬 서버를 시작하지 못했습니다. 로그를 확인해주세요.")
        logging.info("Money Engine stopped")
    finally:
        if opener:
            opener.join(timeout=1)
        if server_socket:
            server_socket.close()
        try:
            state = json.loads(runtime_path().read_text(encoding="utf-8"))
            if state.get("token") == token:
                runtime_path().unlink(missing_ok=True)
        except (OSError, ValueError):
            pass
        lock.close()


def main():
    try:
        run()
    except Exception as exc:
        details = traceback.format_exc()
        try:
            log_file = configure_logging()
            logging.error("Startup failed: %s\n%s", exc, details)
            log_location = str(log_file)
        except Exception:
            log_location = "로그 파일을 저장할 수 없습니다."
        show_error(f"프로그램을 시작하지 못했습니다.\n\n{exc}\n\n로그: {log_location}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
