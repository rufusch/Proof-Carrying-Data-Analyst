"""Fixed container entry point. Receives typed JSON, never arbitrary code."""
import json
import os
import resource
import sys
import tempfile
import time
from pathlib import Path

from .common import canonical, digest
from .engine import execute, UnsafePlan
from .ingest import ingest, IngestError


def isolation_checks(require_inputs=False):
    # Runtime assertions complement Docker configuration; no network interface except loopback.
    interfaces = {p.name for p in Path("/sys/class/net").iterdir()}
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
    group = Path("/sys/fs/cgroup")
    quota, period = (group / "cpu.max").read_text().split()
    memory = (group / "memory.max").read_text().strip()
    pids = (group / "pids.max").read_text().strip()
    with tempfile.TemporaryFile(dir="/tmp") as scratch:
        scratch.write(b"probe")
        scratch.flush()
    checks = {"no_network": not bool(interfaces - {"lo"}), "non_root": os.getuid() == 65534,
        "root_read_only": bool(os.statvfs("/").f_flag & os.ST_RDONLY),
        "capabilities_dropped": int(status["CapEff"].strip(), 16) == 0,
        "no_new_privileges": status["NoNewPrivs"].strip() == "1",
        "seccomp": status["Seccomp"].strip() == "2",
        "memory_limited": memory.isdigit() and 0 < int(memory) <= 768 * 1024**2,
        "pids_limited": pids.isdigit() and 0 < int(pids) <= 64,
        "cpu_limited": quota.isdigit() and 0 < int(quota) <= int(period),
        "cpu_time_limited": resource.getrlimit(resource.RLIMIT_CPU) == (60, 60), "scratch_writable": True}
    if require_inputs:
        checks["inputs_read_only"] = bool(os.statvfs("/inputs").f_flag & os.ST_RDONLY)
    return checks


def main():
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    start = time.monotonic()
    request = json.load(sys.stdin)
    root = Path("/inputs")
    checks = isolation_checks(require_inputs=request["mode"] != "probe")
    if request["mode"] != "probe" and not all(checks.values()):
        raise ValueError("Sandbox isolation requirements are not met.")
    if request["mode"] == "probe":
        result = {"ready": all(checks.values()), "checks": checks}
    elif request["mode"] == "ingest":
        result = ingest(root, request["dataset"], request["limits"])
    elif request["mode"] == "execute":
        for file in request["dataset"]["files"]:
            if digest((root / file["id"]).read_bytes()) != file["sha256"]:
                raise ValueError("Source file hash mismatch.")
        data = (root / "tables.json").read_bytes()
        if digest(data) != request["tables_hash"]:
            raise ValueError("Normalized table hash mismatch.")
        result = execute(request["query"], request["profile"], json.loads(data))
    else:
        raise ValueError("Unsupported sandbox operation.")
    usage = resource.getrusage(resource.RUSAGE_SELF)
    result["resources"] = {"duration_ms": int((time.monotonic() - start) * 1000), "cpu_time_ms": int((usage.ru_utime + usage.ru_stime) * 1000), "peak_memory_bytes": usage.ru_maxrss * 1024}
    print(canonical(result))


if __name__ == "__main__":
    try:
        main()
    except (IngestError, UnsafePlan) as exc:
        print(canonical({"rejected": str(exc)}))
    except Exception:
        # No exception text from parsers/native libraries is sent across the boundary.
        print('{"error":"SANDBOX_OPERATION_FAILED"}')
        sys.exit(1)
