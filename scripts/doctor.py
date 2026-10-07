"""Report deployment prerequisites without printing credentials or raw data."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.config import Settings
from backend.sandbox import DockerSandbox


def check(settings, model=False):
    sandbox = DockerSandbox(settings)
    checks = {"python": sys.version_info >= (3, 12), "docker_cli": False, "linux_engine": False, "sandbox_image": False, "sandbox_isolation": False}
    try:
        version = subprocess.run(sandbox.command("version", "--format", "{{json .}}"), capture_output=True, text=True, timeout=20)
        checks["docker_cli"] = bool(version.stdout)
        if version.returncode == 0:
            info = json.loads(version.stdout)
            checks["linux_engine"] = info.get("Server", {}).get("Os") == "linux"
        if checks["linux_engine"]:
            image = subprocess.run(sandbox.command("image", "inspect", settings.sandbox_image), capture_output=True, timeout=20)
            checks["sandbox_image"] = image.returncode == 0
            if checks["sandbox_image"]:
                checks["sandbox_isolation"] = sandbox.ready()
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    if model:
        from backend.planner import Planner
        checks["model"] = Planner(settings).ready()
    return checks


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", action="store_true", help="Also probe configured model access; may make a network request.")
    args = parser.parse_args()
    checks = check(Settings(), args.model)
    print(json.dumps({"ready": all(checks.values()), "checks": checks}, indent=2))
    raise SystemExit(0 if all(checks.values()) else 1)
