import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from backend.api import create_app
from backend.common import digest
from backend.config import Settings
from backend.engine import execute, UnsafePlan
from backend.ingest import ingest, IngestError
from backend.sandbox import SandboxRejected
from backend.store import Store
from backend.worker import Worker


class InProcessTestSandbox:
    """TEST ONLY. Exercises real parsers/math; does not claim to test OS isolation."""
    def __init__(self):
        self.corrupt = False

    def ready(self):
        return True

    def run(self, root, request):
        try:
            return self._run(root, request)
        except (UnsafePlan, IngestError) as exc:
            raise SandboxRejected(str(exc)) from exc

    def _run(self, root, request):
        if request["mode"] == "ingest":
            return ingest(root, request["dataset"], request["limits"])
        assert digest((root / "tables.json").read_bytes()) == request["tables_hash"]
        for file in request["dataset"]["files"]:
            assert digest((root / file["id"]).read_bytes()) == file["sha256"]
        output = execute(request["query"], request["profile"], json.loads((root / "tables.json").read_text()))
        if self.corrupt:
            output["independent_passed"] = False
        output["resources"] = {"duration_ms": 1, "cpu_time_ms": 1, "peak_memory_bytes": 1024}
        return output


@pytest.fixture
def system(tmp_path):
    settings = Settings(data_dir=tmp_path, database_url="sqlite:///" + (tmp_path / "state.db").as_posix())
    store = Store(settings.database_url)
    sandbox = InProcessTestSandbox()
    worker = Worker(settings, store, sandbox=sandbox)
    with TestClient(create_app(settings, store)) as client:
        yield client, worker, store, settings, sandbox
    store.engine.dispose()


def dataset(system, files=None):
    client, worker, *_ = system
    response = client.post("/api/v1/datasets", files=files or [("files", ("sales.csv", b"region,amount\nWest,10\nWest,20\nEast,5\n", "text/csv"))])
    assert response.status_code == 202, response.text
    id = response.json()["id"]
    worker.run_once()
    assert client.get(f"/api/v1/datasets/{id}").json()["status"] == "ready"
    return id


def analysis(system, dataset_id, question="sum amount", **kwargs):
    client, worker, *_ = system
    response = client.post("/api/v1/analyses", json={"dataset_id": dataset_id, "question": question}, **kwargs)
    assert response.status_code == 202, response.text
    id = response.json()["id"]
    worker.run_once()
    return id
