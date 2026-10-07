import json
from pathlib import Path
from backend.contract import validate


def test_acceptance_fixture_contracts_and_claim_references():
    root = Path(__file__).resolve().parents[1] / "docs/fixtures"
    fixtures = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in root.glob("*.json")}
    assert set(fixtures) == {"happy_path", "clarification", "data_warnings", "refused", "processing_failure"}
    schemas = {"dataset": "Dataset", "profile": "DatasetProfile", "analysis": "Analysis", "result": "AnalysisResult", "evidence": "EvidencePackage"}
    for fixture in fixtures.values():
        for key, value in fixture.items():
            validate(schemas[key], value)
        if "result" not in fixture:
            continue
        if fixture["result"]["outcome"] == "answered":
            assert fixture["result"]["verification"]["status"] == "passed"
            claims = {c["id"] for c in fixture["evidence"]["claims"]}
            for value in fixture["result"]["metrics"] + fixture["result"]["tables"] + fixture["result"]["charts"]:
                assert value["evidence_refs"] and set(value["evidence_refs"]) <= claims
    happy = fixtures["happy_path"]["result"]
    assert happy["metrics"] and happy["tables"] and happy["charts"] and happy["confidence"]["band"] == "high"
    assert fixtures["data_warnings"]["result"]["confidence"]["band"] == "medium"
    assert fixtures["refused"]["evidence"]["execution"]["attempt_count"] == 3
