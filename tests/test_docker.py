"""Explicit opt-in integration test. Does not silently use the in-process double."""
import os
import json
import subprocess
import pytest
from backend.common import uid, digest, canonical
from backend.config import Settings
from backend.sandbox import DockerSandbox


@pytest.mark.docker
@pytest.mark.skipif(os.getenv("RUN_DOCKER_TESTS") != "1", reason="Set RUN_DOCKER_TESTS=1 after building the sandbox image.")
def test_real_isolated_ingestion_execution_and_rerun(tmp_path):
    tmp_path.chmod(0o755)
    sandbox = DockerSandbox(Settings(data_dir=tmp_path))
    probe = sandbox.run(None, {"mode": "probe"})
    assert probe["ready"] and all(probe["checks"].values()), probe
    raw = b"group,amount\nA,2\nA,3\n"
    file = {"id": uid(), "filename": "a.csv", "media_type": "text/csv", "size_bytes": len(raw), "sha256": digest(raw)}
    (tmp_path / file["id"]).write_bytes(raw)
    (tmp_path / file["id"]).chmod(0o644)
    dataset = {"id": uid(), "files": [file]}
    output = sandbox.run(tmp_path, {"mode": "ingest", "dataset": dataset, "limits": {"max_rows": 100, "max_columns": 10, "max_tables": 10}})
    content = canonical(output["tables"]).encode()
    (tmp_path / "tables.json").write_bytes(content)
    (tmp_path / "tables.json").chmod(0o644)
    query = {"table": output["profile"]["tables"][0]["id"], "operation": "sum", "column": "amount", "group_by": None, "filters": [], "join": None}
    request = {"mode": "execute", "dataset": dataset, "profile": output["profile"], "tables_hash": digest(content), "query": query}
    first, second = sandbox.run(tmp_path, request), sandbox.run(tmp_path, request)
    assert first["rows"] == [{"value": 5}]
    assert first["independent_passed"] and first["output_hash"] == second["output_hash"]
    assert (tmp_path / file["id"]).read_bytes() == raw


@pytest.mark.docker
@pytest.mark.skipif(os.getenv("RUN_DOCKER_TESTS") != "1", reason="Requires the real sandbox image.")
def test_container_refuses_work_when_isolation_is_weakened(tmp_path):
    sandbox = DockerSandbox(Settings(data_dir=tmp_path))
    raw = b"amount\n5\n"
    file = {"id": uid(), "filename": "a.csv", "media_type": "text/csv", "size_bytes": len(raw), "sha256": digest(raw)}
    (tmp_path / file["id"]).write_bytes(raw)
    request = {"dataset": {"id": uid(), "files": [file]}, "limits": {"max_rows": 100, "max_columns": 10, "max_tables": 10}}
    # Intentionally omit read-only root, limits, dropped capabilities and no-new-privileges.
    # Probe reports the deficiency; ingest must fail before inspecting its dataset.
    for mode in ["probe", "ingest", "execute"]:
        result = subprocess.run(sandbox.command("run", "--rm", "--network=none", "--mount",
            f"type=bind,src={sandbox.input_root(tmp_path)},dst=/inputs,readonly", "-i", sandbox.settings.sandbox_image),
            input=json.dumps({**request, "mode": mode}), capture_output=True, text=True, timeout=90)
        body = json.loads(result.stdout)
        if mode == "probe":
            assert result.returncode == 0 and body["ready"] is False
            assert body["checks"]["root_read_only"] is False
        else:
            assert result.returncode != 0 and body == {"error": "SANDBOX_OPERATION_FAILED"}
