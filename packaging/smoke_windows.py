"""Run the actual packaged EXE on Windows before distributing it."""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


def request(url, payload=None, method="GET", local=False):
    headers = {"Content-Type": "application/json"}
    if local:
        headers["X-Money-Engine"] = "local-ui"
    data = json.dumps(payload).encode() if payload is not None else None
    with urllib.request.urlopen(urllib.request.Request(url, data=data, method=method,
                                                      headers=headers), timeout=4) as response:
        return json.load(response)


def start(executable, data, env):
    process = subprocess.Popen([str(executable)], env=env)
    runtime = data / "runtime.json"
    try:
        for _ in range(150):
            if process.poll() is not None:
                raise RuntimeError(f"MoneyEngine.exe exited early: {process.returncode}")
            try:
                state = json.loads(runtime.read_text(encoding="utf-8"))
                url = f"http://127.0.0.1:{state['port']}"
                if request(url + "/api/health").get("app") == "MoneyEngineV3":
                    return process, url
            except (OSError, ValueError, KeyError):
                pass
            time.sleep(0.1)
        raise RuntimeError("Packaged server did not become ready")
    except Exception:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
        raise


def main():
    executable = Path(sys.argv[1]).resolve()
    if not executable.is_file():
        raise FileNotFoundError(executable)
    with tempfile.TemporaryDirectory() as temp:
        package = Path(temp) / "MoneyEngine-Windows"
        shutil.copytree(executable.parent, package)
        executable = package / "MoneyEngine.exe"
        data = package / "data"
        logs = package / "logs"
        assert (package / "_internal/frontend/dist/index.html").is_file()
        env = os.environ.copy()
        env.update(MONEY_ENGINE_DISABLE_BROWSER="1")
        env.pop("MONEY_ENGINE_DATA_DIR", None)
        env.pop("MONEY_ENGINE_LOGS_DIR", None)
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            blocker.bind(("127.0.0.1", 8000))
            blocker.listen(1)
        except OSError:
            blocker.close()
            blocker = None  # The port is already occupied, so fallback is still exercised.
        try:
            process, url = start(executable, data, env)
        finally:
            if blocker:
                blocker.close()
        try:
            assert url != "http://127.0.0.1:8000", "The occupied port should be skipped"
            with urllib.request.urlopen(url, timeout=4) as response:
                assert b"<html" in response.read().lower()
            created = request(url + "/api/contents", {"input_source": "보은 전기차 보조금"}, "POST")
            assert len(created["steps"]) == 16
            for _ in range(50):
                if request(url + "/api/contents/" + created["id"])["run"]["status"] == "FAILED":
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError("Missing-key pipeline did not finish before key storage")
            assert (data / "money_engine.sqlite3").is_file()
            assert (logs / "money-engine.log").is_file()
            request(url + "/api/settings/openai-key", {"value": "test-key-only"}, "POST", local=True)
            encrypted = (data / "openai-key.dpapi").read_bytes()
            assert b"test-key-only" not in encrypted
            assert request(url + "/api/settings")["openai_key_configured"]
            duplicate = subprocess.run([str(executable)], env=env, timeout=12)
            assert duplicate.returncode == 0
            assert request(url + "/api/health")["status"] == "ok"
            request(url + "/api/system/stop", method="POST", local=True)
            process.wait(timeout=12)
            assert process.returncode == 0
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)

        process, url = start(executable, data, env)
        try:
            reopened = request(url + "/api/contents/" + created["id"])
            assert reopened["input_source"] == "보은 전기차 보조금"
            assert request(url + "/api/settings")["openai_key_configured"]
            request(url + "/api/system/stop", method="POST", local=True)
            process.wait(timeout=12)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
    print("Packaged EXE: frontend, port fallback, external data/logs, encrypted key, duplicate, restart, quit OK")


if __name__ == "__main__":
    main()
