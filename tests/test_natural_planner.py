import json
from pathlib import Path
import pytest
from backend.planner import Planner
from backend.intent_model import predict, load
from backend.contract import validate
from conftest import dataset, analysis


@pytest.mark.parametrize("question,expected", [
    ("How much revenue did we make?", 150),
    ("Could you show me total sales?", 150),
    ("What is our average revenue?", 75),
    ("What is the highest sales?", 100),
    ("What is the lowest revenue?", 50),
    ("How many orders do we have?", 2),
    ("What is our total order amount?", 150),
])
def test_broader_questions_ground_renamed_columns(system, question, expected):
    did = dataset(system, [("files", ("orders.csv", b"Order_ID,OrderAmount,SalesTerritory\nA,100,West\nB,50,East\n"))])
    aid = analysis(system, did, question)
    result = system[0].get(f"/api/v1/analyses/{aid}/result").json()
    assert result["outcome"] == "answered", result
    assert result["metrics"][0]["value"] == expected
    assert result["verification"]["status"] == "passed"
    assert any('Order' in a["text"] for a in result["assumptions"])


def test_broad_grouping_and_semantic_mapping(system):
    did = dataset(system, [("files", ("orders.csv", b"Total_Sales,SalesTerritory\n100,West\n50,East\n25,West\n"))])
    aid = analysis(system, did, "How much revenue did we generate by region?")
    result = system[0].get(f"/api/v1/analyses/{aid}/result").json()
    assert result["outcome"] == "answered", result
    assert result["tables"][0]["rows"] == [{"group_value": "East", "value": 50}, {"group_value": "West", "value": 125}]


def test_ambiguous_gross_net_metric_requires_actual_choice(system):
    did = dataset(system, [("files", ("orders.csv", b"GrossSales,NetSales\n100,80\n50,40\n"))])
    aid = analysis(system, did, "How much revenue did we make?")
    client, worker, *_ = system
    state = client.get(f"/api/v1/analyses/{aid}").json()
    assert state["status"] == "needs_clarification"
    clarification = state["clarification"]
    choice = next(x["id"] for x in clarification["choices"] if x["label"] == "NetSales")
    assert client.post(f"/api/v1/analyses/{aid}/clarifications", json={"clarification_id": clarification["id"], "choice_id": choice}).status_code == 202
    worker.run_once()
    result = client.get(f"/api/v1/analyses/{aid}/result").json()
    assert result["metrics"][0]["value"] == 120


def test_multiple_csv_files_keep_independent_tables_and_correct_scope(system):
    did = dataset(system, [("files", ("orders.csv", b"OrderAmount,Region\n100,West\n50,East\n")),
                           ("files", ("costs.csv", b"Expenses,Department\n20,Sales\n30,IT\n")),
                           ("files", ("other-orders.csv", b"Sales,Region\n999,West\n"))])
    client, worker, *_ = system
    profile = client.get(f"/api/v1/datasets/{did}/profile").json()
    assert len(profile["tables"]) == 3
    costs = analysis(system, did, "What are our total costs?")
    assert client.get(f"/api/v1/analyses/{costs}/result").json()["metrics"][0]["value"] == 50
    aid = analysis(system, did, "How much revenue did we make?")
    state = client.get(f"/api/v1/analyses/{aid}").json()
    assert state["status"] == "needs_clarification"
    choices = state["clarification"]["choices"]
    assert {x["label"] for x in choices} == {"orders", "other-orders"}
    selected = next(x for x in choices if x["label"] == "orders")
    client.post(f"/api/v1/analyses/{aid}/clarifications", json={"clarification_id": state["clarification"]["id"], "choice_id": selected["id"]})
    worker.run_once()
    result = client.get(f"/api/v1/analyses/{aid}/result").json()
    assert result["verification"]["status"] == "passed" and result["metrics"][0]["value"] == 150


def test_disabled_assumptions_require_confirmation(system):
    did = dataset(system, [("files", ("orders.csv", b"OrderAmount\n100\n50\n"))])
    client, worker, *_ = system
    response = client.post("/api/v1/analyses", json={"dataset_id": did, "question": "How much revenue did we make?", "preferences": {"allow_explicit_assumptions": False}})
    assert response.status_code == 202
    aid = response.json()["id"]
    worker.run_once()
    state = client.get(f"/api/v1/analyses/{aid}").json()
    assert state["status"] == "needs_clarification"
    clarification = state["clarification"]
    client.post(f"/api/v1/analyses/{aid}/clarifications", json={"clarification_id": clarification["id"], "choice_id": clarification["choices"][0]["id"]})
    worker.run_once()
    result = client.get(f"/api/v1/analyses/{aid}/result").json()
    assert result["outcome"] == "answered", result


@pytest.mark.parametrize("question", ["How much revenue did we make and why?", "How much revenue did we make last year?", "What is our total net revenue?", "How much revenue did we make and predict tomorrow?", "How many unique customers do we have?", "What is the total revenue and profit?"])
def test_no_silent_loss_of_qualifiers_or_requested_outputs(system, question):
    did = dataset(system, [("files", ("sales.csv", b"Sales,Region\n100,West\n50,East\n"))])
    aid = analysis(system, did, question)
    result = system[0].get(f"/api/v1/analyses/{aid}/result").json()
    assert result["outcome"] == "refused", result


def test_semantic_advanced_aggregates(system):
    did = dataset(system, [("files", ("orders.csv", b"OrderAmount,Quantity\n100,10\n50,5\n"))])
    aid = analysis(system, did, "sum revenue and mean sales and sum units")
    result = system[0].get(f"/api/v1/analyses/{aid}/result").json()
    assert [x["value"] for x in result["metrics"]] == [150, 75, 15]
    validate("AnalysisResult", result)


def test_training_model_is_reproducible_and_abstains():
    from scripts.train_intent_model import HELD_OUT, training_examples
    from backend.intent_model import train
    training = training_examples()
    assert train(training)["idf"] == load()["idf"]
    for intent, examples in HELD_OUT.items():
        for example in examples:
            assert predict(example)[0] == intent
    assert predict("banana ecosystem")[0] is None


@pytest.mark.parametrize("header", ["DiscountAmount", "RefundAmount", "CostAmount", "TaxAmount", "ProfitAmount", "UnitPrice"])
def test_other_financial_amounts_are_not_assumed_to_be_revenue(system, header):
    did = dataset(system, [("files", ("data.csv", (header + "\n10\n20\n").encode()))])
    aid = analysis(system, did, "How much revenue did we make?")
    assert system[0].get(f"/api/v1/analyses/{aid}/result").json()["outcome"] == "refused"


def test_per_does_not_silently_become_grouping(system):
    did = dataset(system, [("files", ("data.csv", b"OrderAmount,Order_ID\n10,A\n20,B\n"))])
    aid = analysis(system, did, "What is our average sales per order?")
    result = system[0].get(f"/api/v1/analyses/{aid}/result").json()
    assert result["outcome"] == "refused" and "Per" in result["narrative"]
