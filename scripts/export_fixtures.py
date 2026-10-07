"""Generate contract-validated frontend fixtures with the real math and a TEST double.

Run from repository root: python scripts/export_fixtures.py
No production service uses this in-process runner.
"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from fastapi.testclient import TestClient
from conftest import InProcessTestSandbox
from backend.api import create_app
from backend.config import Settings
from backend.contract import validate
from backend.store import Store
from backend.worker import Worker
from backend.sandbox import SandboxUnavailable


def main():
    output = ROOT / "docs" / "fixtures"
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        settings = Settings(data_dir=Path(temp), database_url="sqlite:///" + (Path(temp) / "state.db").as_posix())
        store = Store(settings.database_url)
        sandbox = InProcessTestSandbox()
        worker = Worker(settings, store, sandbox)
        with TestClient(create_app(settings, store)) as client:
            def run(name, raw, question, extra=None):
                files = [("files", ("sales.csv", raw))]
                if extra:
                    files.append(("files", ("reference.csv", extra)))
                accepted = client.post("/api/v1/datasets", files=files).json()
                worker.run_once()
                did = accepted["id"]
                upload = client.get(f"/api/v1/datasets/{did}").json()
                profile = client.get(f"/api/v1/datasets/{did}/profile").json()
                analysis = client.post("/api/v1/analyses", json={"dataset_id": did, "question": question}).json()
                worker.run_once()
                aid = analysis["id"]
                state = client.get(f"/api/v1/analyses/{aid}").json()
                fixture = {"dataset": validate("Dataset", upload), "profile": validate("DatasetProfile", profile), "analysis": validate("Analysis", state)}
                for route, schema in [("result", "AnalysisResult"), ("evidence", "EvidencePackage")]:
                    response = client.get(f"/api/v1/analyses/{aid}/{route}")
                    if response.status_code == 200:
                        fixture[route] = validate(schema, response.json())
                (output / (name + ".json")).write_text(json.dumps(fixture, indent=2, ensure_ascii=False), encoding="utf-8")

            run("happy_path", b"region,amount\nWest,10\nWest,20\nEast,5\n", "sum amount by region", b"id,label\n1,reference\n")
            run("clarification", b"region,amount\nWest,10\n", "sum amount", b"region,amount\nEast,20\n")
            run("data_warnings", b"region,amount,date,currency\nWest,10,01/02/2025,USD 10\nWest,,02/03/2025,EUR 10\n" + b"West,,,\n" * 12, "sum amount")
            sandbox.corrupt = True
            run("refused", b"amount\n10\n", "sum amount")
            sandbox.corrupt = False
            original = sandbox.run
            def unavailable(root, request):
                if request["mode"] == "execute":
                    raise SandboxUnavailable("Worker unavailable")
                return original(root, request)
            sandbox.run = unavailable
            run("processing_failure", b"amount\n10\n", "sum amount")
        store.engine.dispose()
    print("Exported five OpenAPI-validated fixture bundles.")


if __name__ == "__main__":
    main()
