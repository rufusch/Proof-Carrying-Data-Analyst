"""Reproducible supervised training and held-out evaluation, without credentials."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.common import digest
from backend.intent_model import MODEL_PATH, train, predict

TRAINING = {
    "sum": ["sum metric", "total metric", "show total metric", "calculate total metric", "what is the total metric", "how much metric did we make", "how much metric did we generate", "how much metric do we have", "what are our total metric", "give me total metric", "add up metric", "break down total metric", "what is our overall metric", "show the combined metric", "how much metric was recorded"],
    "mean": ["average metric", "mean metric", "show average metric", "calculate average metric", "what is the average metric", "what is the mean metric", "what is the typical metric", "on average how much metric", "how much metric on average", "give me average metric", "break down average metric", "what is our average metric", "show the mean metric", "find the average metric", "average value of metric"],
    "count": ["count metric", "how many metric do we have", "how many metric are there", "how many metric were recorded", "how many metric did we receive", "what is the number of metric", "show the number of metric", "count all metric", "number of metric", "give me a count of metric", "how many metric did we get", "how many metric did we process", "count the metric", "how many metric exist", "tell me the number of metric"],
    "min": ["minimum metric", "min metric", "lowest metric", "smallest metric", "what is the lowest metric", "what is the minimum metric", "show the smallest metric", "find the lowest metric", "give me the minimum metric", "what was the smallest metric", "least metric", "show minimum metric", "calculate minimum metric", "find minimum metric", "lowest value of metric"],
    "max": ["maximum metric", "max metric", "highest metric", "largest metric", "what is the highest metric", "what is the maximum metric", "show the largest metric", "find the highest metric", "give me the maximum metric", "what was the largest metric", "greatest metric", "show maximum metric", "calculate maximum metric", "find maximum metric", "highest value of metric"],
}
TRAINING["sum"] += ["what is metric", "what is the metric", "what was metric", "what is metric made", "what is the metric made", "show metric", "show me metric", "tell me metric", "give me metric"]
HELD_OUT = {
    "sum": ["tell me total metric", "what was our total metric", "how much metric did we earn", "total value of metric"],
    "mean": ["tell me average metric", "what was our average metric", "show me the typical metric", "find mean metric"],
    "count": ["how many metric have we recorded", "tell me how many metric there are", "show me a count of metric", "what was the number of metric"],
    "min": ["tell me the lowest metric", "show me minimum metric", "find the smallest metric", "what was our minimum metric"],
    "max": ["tell me the highest metric", "show me maximum metric", "find the largest metric", "what was our maximum metric"],
}


def training_examples():
    examples = [{"text": text + suffix, "intent": intent} for intent, texts in TRAINING.items() for text in texts for suffix in ["", " by group"]]
    adapted = ROOT / "docs/dataset-planner-training.json"
    if adapted.exists():
        examples += json.loads(adapted.read_text(encoding="utf-8"))["examples"]
    return examples


def main():
    examples = training_examples()
    held_out = [{"text": text + suffix, "intent": intent} for intent, texts in HELD_OUT.items() for text in texts for suffix in ["", " by group"]]
    assert not {x["text"] for x in examples} & {x["text"] for x in held_out}
    model = train(examples)
    model["training_sha256"] = digest(examples)
    results = [{**x, "predicted": predict(x["text"], model)[0]} for x in held_out]
    failures = [x for x in results if x["intent"] != x["predicted"]]
    report = {"training_examples": len(examples), "held_out_examples": len(held_out), "correct": len(results) - len(failures), "failures": failures, "training_sha256": model["training_sha256"], "scope": "Synthetic English aggregate-intent paraphrases; not a general language model benchmark."}
    if failures:
        print(json.dumps(report, indent=2))
        raise SystemExit("Intent model evaluation failed; existing model preserved.")
    MODEL_PATH.write_text(json.dumps(model, sort_keys=True) + "\n", encoding="utf-8")
    output = ROOT / "docs" / "intent-model-evaluation.json"
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
