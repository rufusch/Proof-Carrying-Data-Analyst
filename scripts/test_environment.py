"""Isolated local API/worker supervisor. Lifecycle is controlled by its own stop file."""
import argparse
import json
import os
import socket
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".test-env"


def supervise(token):
    state = json.loads((STATE / "environment.json").read_text())
    if state["token"] != token:
        raise RuntimeError("Environment ownership changed")
    data = (STATE / "data").resolve()
    data.mkdir(exist_ok=True)
    env = {**os.environ, "DATA_DIR": str(data), "DATABASE_URL": "sqlite:///" + (data / "state.db").as_posix(), "PLANNER": "deterministic", "PYTHONUNBUFFERED": "1"}
    runtime_path = STATE / "runtime.json"
    if runtime_path.exists():
        runtime = json.loads(runtime_path.read_text())
        env.update({key: value for key, value in runtime.items() if key in {"DOCKER_WSL_DISTRO", "DOCKER_EXECUTABLE", "SANDBOX_IMAGE"}})
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    children, streams = [], []
    try:
        for role, command in [("api", ["-m", "uvicorn", "backend.api:app", "--host", "127.0.0.1", "--port", str(state["port"])]), ("worker", ["-m", "backend.worker"])]:
            log = (STATE / (role + ".log")).open("ab", buffering=0)
            streams.append(log)
            children.append(subprocess.Popen([sys.executable, *command], cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=flags))
        state.update(status="running", supervisor_pid=os.getpid(), child_pids=[p.pid for p in children])
        (STATE / "environment.json").write_text(json.dumps(state, indent=2))
        while not (STATE / "stop-request").exists():
            (STATE / "supervisor-heartbeat").touch()
            if any(child.poll() is not None for child in children):
                state["status"] = "failed"
                break
            time.sleep(.5)
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        for log in streams:
            log.close()
        if state.get("status") != "failed":
            state["status"] = "stopped"
        (STATE / "environment.json").write_text(json.dumps(state, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "stop", "status", "supervise"])
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--token")
    args = parser.parse_args()
    STATE.mkdir(exist_ok=True)
    path = STATE / "environment.json"
    if args.action == "supervise":
        supervise(args.token)
        return
    state = json.loads(path.read_text()) if path.exists() else {"status": "not_started"}
    if state["status"] in {"running", "starting"}:
        heartbeat = STATE / "supervisor-heartbeat"
        last_seen = max(path.stat().st_mtime, heartbeat.stat().st_mtime if heartbeat.exists() else 0)
        if time.time() - last_seen > 60:
            try:
                with socket.create_connection(("127.0.0.1", state["port"]), timeout=1):
                    pass
            except OSError:
                state["status"] = "stopped"
                state["reason"] = "Processes stopped; stale saved status recovered."
                path.write_text(json.dumps(state, indent=2))
    if args.action == "status":
        print(json.dumps(state, indent=2))
        return
    if args.action == "stop":
        (STATE / "stop-request").touch()
        print("Shutdown requested; only this test environment's child processes will be stopped.")
        return
    if state["status"] in {"running", "starting"}:
        raise SystemExit("The test environment is already running or starting. Stop it before restarting.")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", args.port))
    (STATE / "stop-request").unlink(missing_ok=True)
    state = {"status": "starting", "token": str(uuid.uuid4()), "port": args.port, "url": f"http://127.0.0.1:{args.port}/api/v1"}
    path.write_text(json.dumps(state, indent=2))
    with (STATE / "supervisor.log").open("ab", buffering=0) as log:
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS if os.name == "nt" else 0
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "supervise", "--token", state["token"]], cwd=ROOT,
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=flags, start_new_session=os.name != "nt")
    print(f"Test frontend starting at http://127.0.0.1:{args.port}/")
    print("API documentation: " + state["url"] + "/docs")


if __name__ == "__main__":
    main()
