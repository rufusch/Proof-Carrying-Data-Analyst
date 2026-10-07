# Broader questions and multiple CSV inputs

Upload multiple CSV files through the existing multipart `files` field or select
several files in the frontend. Each file becomes a separate table with its own
profile and source hash. The effective limit remains ten files. Matching schemas
do not imply that files should be combined; choose the intended table when asked.
The optional OpenAI planner supports explicitly confirmed joins within the
existing allowlist. Cross-file unions and automatic joins are not introduced.

The default planner now uses a trained local TF-IDF nearest-example intent model
for everyday aggregate phrasing and conservative semantic grounding. It is a
small supervised classifier, not a general-purpose language model. It learns
sum/average/count/minimum/maximum intent from generic examples and authorized
Tesla workbook schema-based examples (233 currently) and
uses a similarity and margin threshold to abstain on uncertain intent. No uploaded
files or user questions are added to training automatically.

Schema grounding is a separate transparent rules layer: camelCase and snake_case
normalization, plural variants and bounded domain synonyms. For example, revenue
can map to `OrderAmount`, sales to `Total_Sales`, and region to `SalesTerritory`.
Gross/net qualifiers are preserved. Multiple plausible numeric columns prompt
clarification. Every non-literal mapping is recorded, and requests that disable
assumptions require confirmation. Entity counts use non-null ID/name values;
distinct-entity counts are outside the current engine.

Examples:

* `How much revenue did we make?`
* `Could you show me total sales?`
* `What is our average revenue by region?`
* `What is the highest sales?`
* `How many orders do we have?`
* `sum revenue and mean sales and sum units`

The local parser accepts complete supported phrases. It additionally supports
last/latest and previous calendar quarters based on the latest reporting date
present in the uploaded data. Windows are explicit inclusive-start/exclusive-end
date filters; wall-clock dates are not substituted. Unspecified financial totals
can be answered with an explicit sum assumption and reduced interpretation score.
Disabling assumptions prompts for confirmation. Missing date fields and ambiguous
metric definitions still require clarification or refusal.

Arbitrary date filters,
ranking, unique counts, causal explanations, forecasts and unrecognized clauses
are refused instead of silently dropped. The trained classifier does not expand
the execution allowlist. Calculations still require independent recalculation
and a clean isolated rerun. Intent similarity does not certify arithmetic or
business meaning.

## Training and evaluation

```powershell
.\.venv\Scripts\python.exe -B scripts/train_intent_model.py
.\.venv\Scripts\python.exe -B -m pytest tests/test_natural_planner.py -q
```

The trainer writes `backend/intent_model.json` only after the evaluation gate
passes. Training templates and a separate 40-example paraphrase evaluation are
in `scripts/train_intent_model.py`; their exact texts do not overlap. The feature
pipeline was developed against this small evaluation, so its score is a regression
check, not an independent estimate of performance on arbitrary questions.
`docs/intent-model-evaluation.json` records the result and training-set hash.
`docs/dataset-planner-training.json` records authorized schema-based examples and
the source hash. Training uses question/intent examples, not memorized cell values
or totals. Adaptation can be reproduced with:

```powershell
.\.venv\Scripts\python.exe -B scripts/train_dataset_planner.py "path\to\Tesla_Financial_Report.xlsx"
```

Confidence combines recorded ambiguity, data quality, execution, independent
verification and reproducibility. Vague wording reduces its interpretation factor;
the numerical score is a heuristic assessment, not a calibrated probability.
The tests also cover renamed schemas, ambiguous metrics, preferences, multiple
CSV files, unsupported modifiers and actual verified calculations.

Rebuild the Docker image and restart API/worker after changing planner code or
the model artifact. The image builder and Python package include the artifact.

For more varied language and supported filters/joins, set `PLANNER=openai` and
configure model credentials in both processes. Its prompt now understands schema
synonyms while requiring exact schema names in the typed execution plan.
That existing adapter uses structured outputs; no hosted fine-tuning job is
created by the local trainer. Live OpenAI execution needs separate account access
and validation.
