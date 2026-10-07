import sys
from types import SimpleNamespace

from backend.planner import Planner, Decision, Query, Choice, Join
from backend.config import Settings
from conftest import dataset, analysis


def test_model_adapter_uses_structured_output_without_raw_rows(monkeypatch, tmp_path):
    captured = {}
    decision = Decision(action="refuse", reason="Unsupported calculation", clarification_question=None, choices=[], query=None, assumptions=[])
    def parse(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(output_parsed=decision)
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=lambda **kwargs: SimpleNamespace(responses=SimpleNamespace(parse=parse))))
    planner = Planner(Settings(data_dir=tmp_path, planner="openai", model="test-model"))
    profile = {"tables": [{"id": "t1", "sample_rows": [{"x": "private-row"}], "columns": [{"name": "x", "sample_values": ["private-sample"], "min": "private-min", "max": "private-max"}]}]}
    assert planner.decide("sum x", profile, [], {}) == decision
    assert captured["text_format"] is Decision and captured["store"] is False
    request = captured["input"][1]["content"]
    assert "private-" not in request
    assert profile["tables"][0]["columns"][0]["sample_values"] == ["private-sample"]


def test_ambiguous_metric_model_clarification(system):
    did = dataset(system, [("files", ("money.csv", b"gross_amount,net_amount\n100,80\n50,40\n"))])
    client, worker, *_ = system
    profile = client.get(f"/api/v1/datasets/{did}/profile").json()
    table = profile["tables"][0]["id"]
    class ModelDouble:
        def decide(self, question, profile, answers, preferences, repair=None):
            if not answers:
                return Decision(action="clarify", reason="Both amount columns could define revenue.", clarification_question="Which field defines revenue?",
                    choices=[Choice(id="gross", label="Gross amount", description="Before deductions"), Choice(id="net", label="Net amount", description="After deductions")], query=None, assumptions=[])
            return Decision(action="answer", reason="User selected net amount.", clarification_question=None, choices=[], assumptions=[],
                query=Query(table=table, operation="sum", column="net_amount", group_by=None, filters=[], join=None))
    worker.planner = ModelDouble()
    aid = analysis(system, did, "What is total revenue?")
    clarification = client.get(f"/api/v1/analyses/{aid}").json()["clarification"]
    assert client.post(f"/api/v1/analyses/{aid}/clarifications", json={"clarification_id": clarification["id"], "choice_id": "net"}).status_code == 202
    worker.run_once()
    assert client.get(f"/api/v1/analyses/{aid}/result").json()["metrics"][0]["value"] == 120


def test_model_join_requires_confirmation(system):
    did = dataset(system, [("files", ("orders.csv", b"id,amount\n1,10\n2,20\n")), ("files", ("customers.csv", b"id,region\n1,West\n2,East\n"))])
    client, worker, *_ = system
    tables = client.get(f"/api/v1/datasets/{did}/profile").json()["tables"]
    query = Query(table=tables[0]["id"], operation="sum", column="amount", group_by=tables[1]["id"] + ".region", filters=[], join=Join(table=tables[1]["id"], left_column="id", right_column="id"))
    class ModelDouble:
        def decide(self, *args):
            return Decision(action="answer", reason="Summarize orders by customer region.", clarification_question=None, choices=[], query=query, assumptions=[])
    worker.planner = ModelDouble()
    aid = analysis(system, did, "Sum order amounts by customer region")
    clarification = client.get(f"/api/v1/analyses/{aid}").json()["clarification"]
    assert "inner join" in clarification["question"]
    client.post(f"/api/v1/analyses/{aid}/clarifications", json={"clarification_id": clarification["id"], "choice_id": "confirm_join"})
    worker.run_once()
    result = client.get(f"/api/v1/analyses/{aid}/result").json()
    assert result["outcome"] == "answered"
    assert result["tables"][0]["rows"] == [{"group_value": "East", "value": 20}, {"group_value": "West", "value": 10}]
