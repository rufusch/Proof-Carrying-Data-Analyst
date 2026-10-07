import pytest

from backend.engine import execute, equivalent, UnsafePlan, prepare
from backend.contract import validate
from backend.planner import deterministic, Query, Decision
from test_engine import data, query
from conftest import dataset, analysis


def growth(profile, **overrides):
    return query(profile, operation="growth", period={"column": "day", "baseline_start": "2025-01-01", "baseline_end": "2025-02-01", "current_start": "2025-02-01", "current_end": "2025-03-01"}, **overrides)


def test_grouped_growth_with_explicit_period_edges(tmp_path):
    raw = b"region,amount,day\nWest,100,2025-01-01\nWest,20,2025-01-31\nWest,180,2025-02-01\nWest,900,2025-03-01\nEast,50,2025-01-10\nEast,25,2025-02-28\n"
    profile, tables = data(tmp_path, raw)
    output = execute(growth(profile, group_by="region"), profile, tables)
    assert output["rows"] == [
        {"group_value": "East", "baseline": 50, "current": 25, "change": -25, "value": -50},
        {"group_value": "West", "baseline": 120, "current": 180, "change": 60, "value": 50}]
    assert output["independent_passed"]


@pytest.mark.parametrize("rows,reason", [
    ("0,2025-01-01\n10,2025-02-01\n", "positive baseline"),
    ("-10,2025-01-01\n10,2025-02-01\n", "positive baseline"),
    ("10,2025-01-01\n", "both comparison periods"),
    ("10,2025-02-01\n", "both comparison periods"),
    ("10,2025-01-01\n10,2025-02-01\n,2025-02-02\n", "contain nulls"),
    ("10,2025-01-01\n10,2025-02-01\n20,\n", "null or non-date"),
    ("10,2025-01-01\n10,2025-99-99\n", "valid ISO dates"),
    ("10,2025-01-01T12:00:00\n10,2025-02-01T12:00:00\n", "timezone/time-of-day")])
def test_undefined_growth_refuses(tmp_path, rows, reason):
    profile, tables = data(tmp_path, ("amount,day\n" + rows).encode())
    with pytest.raises(UnsafePlan, match=reason):
        execute(growth(profile), profile, tables)


def test_growth_refuses_overlapping_periods(tmp_path):
    profile, tables = data(tmp_path, b"amount,day\n10,2025-01-01\n20,2025-02-01\n")
    request = growth(profile)
    request["period"]["current_start"] = "2025-01-20"
    with pytest.raises(UnsafePlan, match="non-overlapping"):
        execute(request, profile, tables)


@pytest.mark.parametrize("operation,expected", [("ratio", .25), ("percentage", 25)])
def test_ratio_of_totals_not_mean_of_ratios(tmp_path, operation, expected):
    profile, tables = data(tmp_path, b"amount,denom\n10,20\n15,80\n")
    output = execute(query(profile, operation=operation, denominator="denom"), profile, tables)
    assert output["rows"] == [{"numerator": 25, "denominator": 100, "value": expected}]
    assert output["independent_passed"]


@pytest.mark.parametrize("raw", [b"amount,denom\n1,0\n", b"amount,denom\n1,1\n2,-1\n", b"amount,denom\n1,2\n,3\n"])
def test_ratio_undefined_or_incomplete_refuses(tmp_path, raw):
    profile, tables = data(tmp_path, raw)
    with pytest.raises(UnsafePlan):
        execute(query(profile, operation="ratio", denominator="denom"), profile, tables)


def test_multiple_aggregates_every_value_checked(tmp_path):
    profile, tables = data(tmp_path)
    output = execute(query(profile, additional_aggregates=[{"operation": "mean", "column": "amount"}, {"operation": "count", "column": None}], group_by="category"), profile, tables)
    assert output["independent_passed"]
    assert output["rows"][1] == {"group_value": "B", "value": 10, "metric_2": 10, "metric_3": 2}
    corrupted = [{**row, "metric_2": 999} for row in output["rows"]]
    assert not equivalent(output["rows"], corrupted)


@pytest.mark.parametrize("question", ["ratio of amount to denom", "percentage of amount to denom", "sum amount and mean amount and count rows", "growth of amount from 2025-01 to 2025-02 using day"])
def test_advanced_grammar_and_api_evidence(system, question):
    did = dataset(system, [("files", ("data.csv", b"amount,denom,day\n10,20,2025-01-01\n15,30,2025-02-01\n"))])
    aid = analysis(system, did, question)
    client = system[0]
    result = client.get(f"/api/v1/analyses/{aid}/result").json()
    assert result["outcome"] == "answered", result
    validate("AnalysisResult", result)
    evidence = client.get(f"/api/v1/analyses/{aid}/evidence").json()
    validate("EvidencePackage", evidence)
    assert result["verification"]["status"] == "passed"
    assert len(result["metrics"]) >= 3
    assert all(m["evidence_refs"] for m in result["metrics"])


def test_growth_question_does_not_silently_discard_why(tmp_path):
    profile, _ = data(tmp_path, b"amount,day\n10,2025-01-01\n15,2025-02-01\n")
    decision = deterministic("growth of amount from 2025-01 to 2025-02 using day and explain why", profile, [])
    assert decision.action == "refuse"


def test_repair_cannot_change_the_requested_calculation(system):
    did = dataset(system)
    _, worker, _, _, sandbox = system
    sandbox.corrupt = True
    calls = []
    original = worker.planner.decide
    def decide(*args):
        calls.append(1)
        return original(*args)
    worker.planner.decide = decide
    aid = analysis(system, did)
    assert len(calls) == 1
    assert system[0].get(f"/api/v1/analyses/{aid}/result").json()["outcome"] == "refused"
