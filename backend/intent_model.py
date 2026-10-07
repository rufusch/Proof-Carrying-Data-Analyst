"""Small supervised TF-IDF nearest-example intent model. No uploaded data is trained on."""
from collections import Counter
from functools import lru_cache
import json
import math
from pathlib import Path
import re

MODEL_PATH = Path(__file__).with_name("intent_model.json")


def features(text):
    filler = {"the", "a", "an", "our", "we", "me", "please", "tell", "show", "give", "find", "calculate", "is", "are", "was", "were", "have", "has", "do", "did", "there", "of", "value"}
    words = [w for w in re.findall(r"[a-z_]+", text.casefold()) if w not in filler]
    return Counter(words + [a + " " + b for a, b in zip(words, words[1:])])


def vector(text, idf):
    values = {k: (1 + math.log(v)) * idf[k] for k, v in features(text).items() if k in idf}
    length = math.sqrt(sum(v * v for v in values.values())) or 1
    return {k: v / length for k, v in values.items()}


def train(examples):
    frequency = Counter()
    for item in examples:
        frequency.update(features(item["text"]).keys())
    idf = {k: math.log((1 + len(examples)) / (1 + n)) + 1 for k, n in sorted(frequency.items())}
    return {"version": 1, "algorithm": "tfidf_nearest_example", "training_count": len(examples), "idf": idf,
        "examples": [{"intent": x["intent"], "vector": vector(x["text"], idf)} for x in examples]}


@lru_cache(maxsize=1)
def load():
    model = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
    if model["version"] != 1:
        raise ValueError("Unsupported intent model version")
    return model


def predict(text, model=None):
    model = model or load()
    values = vector(text, model["idf"])
    scores = {}
    for example in model["examples"]:
        score = sum(value * example["vector"].get(key, 0) for key, value in values.items())
        scores[example["intent"]] = max(scores.get(example["intent"], 0), score)
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    if not ranked:
        return None, 0
    intent, score = ranked[0]
    # Similarity is an abstention signal, not a calibrated probability of correctness.
    if score < .55 or score - ranked[1][1] < .07:
        return None, score
    return intent, score
