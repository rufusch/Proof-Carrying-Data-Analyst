"""Boot the real HTTP app on a free local port, probe it, and shut it down."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory() as temp:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        env = {**os.environ, "DATA_DIR": temp, "DATABASE_URL": "sqlite:///" + (Path(temp) / "state.db").as_posix(), "PLANNER": "deterministic"}
        process = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.api:app", "--host", "127.0.0.1", "--port", str(port)], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = f"http://127.0.0.1:{port}/api/v1"
        try:
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError("API exited before readiness probe")
                try:
                    with urllib.request.urlopen(base + "/health", timeout=1) as response:
                        assert response.headers["X-Request-ID"]
                        assert json.load(response)["status"] == "ok"
                    break
                except OSError:
                    time.sleep(.1)
            else:
                raise RuntimeError("API did not start in time")
            with urllib.request.urlopen(base + "/capabilities") as response:
                assert json.load(response)["contract_version"] == "1.0"
            with urllib.request.urlopen(base + "/openapi.json") as response:
                assert json.load(response)["info"]["version"] == "1.0.0"
            try:
                urllib.request.urlopen(base + "/ready")
                raise AssertionError("Expected not-ready without a worker")
            except urllib.error.HTTPError as response:
                assert response.code == 503
                assert json.load(response)["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
            print("Real HTTP smoke passed: health, capabilities, OpenAPI, and missing-worker readiness.")
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    main()
