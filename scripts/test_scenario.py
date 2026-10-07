"""Real HTTP integration scenario against the v1 contract. No sandbox double."""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx
from backend.contract import validate
from scripts.make_test_data import generate


def run(base_url, output):
    sample = generate()
    expected = json.loads((sample / "expected.json").read_text())
    report = {"base_url": base_url, "contract_version": "1.0", "checks": [], "analyses": []}
    with httpx.Client(base_url=base_url, timeout=120) as client:
        def checked(response, schema, status=200):
            assert response.status_code == status, (response.status_code, response.text)
            assert response.headers.get("x-request-id")
            return validate(schema, response.json())
        def wait(path, schema, terminal):
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                current = checked(client.get(path), schema)
                if current["status"] in terminal:
                    return current
                time.sleep(.25)
            raise RuntimeError("Job did not reach its expected state within four minutes.")
        checked(client.get("/health"), "Health")
        checked(client.get("/ready"), "Readiness")
        checked(client.get("/capabilities"), "Capabilities")
        dataset = checked(client.post("/datasets", data={"name": "Synthetic duplicate datasheet test"}, files=[("files", (name, (sample / name).read_bytes(), "text/csv")) for name in ["sales.csv", "sales-duplicate.csv"]]), "Dataset", 202)
        did = dataset["id"]
        ready = wait(f"/datasets/{did}", "Dataset", {"ready", "failed"})
        assert ready["status"] == "ready", ready
        profile = checked(client.get(f"/datasets/{did}/profile"), "DatasetProfile")
        assert len(profile["tables"]) == 2 and ready["files"][0]["sha256"] == ready["files"][1]["sha256"]
        report["checks"].append("Immutable duplicate CSV upload and profile")
        def analyze(question):
            started = checked(client.post("/analyses", json={"dataset_id": did, "question": question}), "Analysis", 202)
            aid = started["id"]
            state = wait(f"/analyses/{aid}", "Analysis", {"needs_clarification", "completed", "refused", "failed"})
            assert state["status"] == "needs_clarification", state
            clarification = state["clarification"]
            answer = {"clarification_id": clarification["id"], "choice_id": clarification["choices"][0]["id"]}
            checked(client.post(f"/analyses/{aid}/clarifications", json=answer), "Analysis", 202)
            checked(client.post(f"/analyses/{aid}/clarifications", json=answer), "ErrorEnvelope", 409)
            terminal = wait(f"/analyses/{aid}", "Analysis", {"completed", "refused", "failed"})
            assert terminal["status"] == "completed", terminal
            result = checked(client.get(f"/analyses/{aid}/result"), "AnalysisResult")
            evidence = checked(client.get(f"/analyses/{aid}/evidence"), "EvidencePackage")
            assert result["outcome"] == "answered" and result["verification"]["status"] == "passed"
            claims = {c["id"] for c in evidence["claims"]}
            assert all(set(item["evidence_refs"]) <= claims for item in result["metrics"] + result["tables"] + result["charts"])
            assert evidence["execution"]["network_enabled"] is False and evidence["execution"]["inputs_read_only"] is True
            report["analyses"].append({"analysis": terminal, "result": result, "evidence": evidence})
            return aid, result
        aid, result = analyze("sum amount and sum units and sum profit")
        assert [m["value"] for m in result["metrics"]] == [expected["total_amount"], expected["total_units"], expected["total_profit"]]
        report["checks"].append("Clarification, stale answer rejection, and three independently verified totals")
        _, growth = analyze("growth of amount from 2025-01 to 2025-02 using day by region")
        assert {r["group_value"]: {k: v for k, v in r.items() if k != "group_value"} for r in growth["tables"][0]["rows"]} == expected["growth_by_region"]
        report["checks"].append("January/February growth by region matches known values")
        _, percentage = analyze("percentage of profit to amount")
        assert next(m["value"] for m in percentage["metrics"] if m["unit"] == "percent") == 20
        report["checks"].append("Ratio-of-totals percentage matches known value")
        original = client.get(f"/analyses/{aid}/evidence").json()
        rerun = checked(client.post(f"/analyses/{aid}/reruns"), "Analysis", 202)
        assert rerun["id"] != aid and rerun["parent_analysis_id"] == aid
        terminal = wait(f'/analyses/{rerun["id"]}', "Analysis", {"completed", "refused", "failed"})
        assert terminal["status"] == "completed", terminal
        rerun_result = checked(client.get(f'/analyses/{rerun["id"]}/result'), "AnalysisResult")
        assert result["verification"]["output_hash"] == rerun_result["verification"]["output_hash"]
        assert client.get(f"/analyses/{aid}/evidence").json() == original
        stream = client.get(f"/analyses/{aid}/events")
        assert stream.status_code == 200 and "event: snapshot" in stream.text
        report["checks"].append("Immutable rerun, reproducible output hash and terminal SSE snapshot")
        excel = checked(client.post("/datasets", files=[("files", ("sales.xlsx", (sample / "sales-with-duplicate-sheet.xlsx").read_bytes()))]), "Dataset", 202)
        excel_ready = wait(f'/datasets/{excel["id"]}', "Dataset", {"ready", "failed"})
        assert excel_ready["status"] == "ready" and excel_ready["table_count"] == 2
        report["checks"].append("Real isolated Excel ingestion exposes both sheets")
        report["dataset_id"] = did
    report["status"] = "passed"
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "passed", "checks": report["checks"], "report": str(output)}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8010/api/v1")
    parser.add_argument("--output", default=str(ROOT / ".test-env/report.json"))
    arguments = parser.parse_args()
    run(arguments.base_url, arguments.output)
