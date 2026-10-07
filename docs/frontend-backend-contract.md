# Proof-Carrying Data Analyst: Frontend-Backend Contract

Status: **MVP contract v1.0**  
Base path: `/api/v1`  
Transport: HTTPS + JSON, except multipart uploads and Server-Sent Events (SSE)  
Machine-readable source: [`openapi.yaml`](./openapi.yaml)

This document is the integration boundary between the Antigravity frontend and the Codex backend. Either side may be implemented against mocks as long as it obeys this contract. Additive fields may appear in responses; clients must ignore fields they do not understand.

## 1. MVP product promise

A user can upload several tabular files, ask a natural-language question, and receive either:

1. a mathematically valid, reproducible answer with generated code and evidence; or
2. a clarification request or precise refusal explaining why a trustworthy answer cannot be produced.

The implementation must be schema-agnostic: it must not depend on pre-agreed column names or a single demo dataset.

### Included in the base MVP

- Multi-file upload: `.csv`, `.xlsx`, and `.xls`; multiple Excel sheets are treated as separate tables.
- Immutable source-file hashes and dataset metadata.
- Dataset profiling: tables, columns, inferred types, nulls, duplicates, sample rows, candidate keys, date/unit/currency warnings, and possible joins.
- Natural-language question submission.
- Ambiguity detection with a structured clarification loop.
- Explicit analysis plan: tables, joins, filters, cleaning, calculations, assumptions, and expected outputs.
- Rerunnable generated DuckDB SQL and/or restricted Python.
- Controlled execution with read-only inputs, no network, resource limits, and an allowlist.
- Mechanical validation, independent recalculation of key results, and one clean rerun.
- A bounded repair loop of at most three attempts.
- Evidence package linking claims to files, tables/rows or aggregate queries, generated code, assumptions, warnings, and verification checks.
- Confidence assessment based on ambiguity, data quality, execution, verification, and reproducibility—not model intuition alone.
- Clear refusal when safety or verification does not pass.

### Explicit MVP limits

- Inputs are tabular files only; PDFs, images, databases, and live URLs are later adapters.
- Maximum 10 files, 50 MiB per file, and 200 MiB per dataset bundle by default. The API returns the effective limits.
- The MVP returns text, scalar metrics, tables, and simple chart specifications. It does not generate arbitrary UI code.
- Authentication and multi-tenant workspaces are deferred. IDs must nevertheless be opaque UUIDs so auth can be added without changing routes.
- Uploaded data and artifacts expire after 24 hours in the MVP unless deployment configuration says otherwise.

## 2. Ownership boundary

| Concern | Frontend owns | Backend owns |
|---|---|---|
| Upload UX | File selection, client-side type/size hints, progress | Authoritative validation, hashing, storage, ingestion |
| Dataset view | Rendering tables, warnings, profile summaries | Parsing, schema/type inference, quality statistics, join suggestions |
| Analysis | Question input, clarification UI, progress display | Intent parsing, planning, code generation, safe execution, retries |
| Correctness | Displays verification state exactly; never labels unverified output as verified | All math, execution, verification, reproducibility, refusal decisions |
| Confidence | Explains dimensions and warnings; does not recompute score | Produces score, band, factors, and score version |
| Evidence | Drill-down UI, code viewer, downloads | Immutable evidence manifest, source locators, hashes, logs |
| Secrets | No model/API secrets in browser code | Holds model keys and all privileged credentials server-side |

The frontend must never infer success from prose. It uses `status`, `outcome`, `verification.status`, and `confidence.band`.

## 3. Canonical user flow

1. `GET /capabilities` to obtain effective file limits and supported formats.
2. `POST /datasets` with one or more files and an optional display name.
3. Poll `GET /datasets/{dataset_id}` until `status` is `ready` or `failed`.
4. Render `GET /datasets/{dataset_id}/profile`.
5. `POST /analyses` with `dataset_id` and `question`.
6. Follow progress using SSE at `GET /analyses/{analysis_id}/events`; polling `GET /analyses/{analysis_id}` is the required fallback.
7. If status is `needs_clarification`, render the returned question and choices, then call `POST /analyses/{analysis_id}/clarifications`.
8. When terminal, fetch `GET /analyses/{analysis_id}/result` and, when available, `GET /analyses/{analysis_id}/evidence`.
9. `POST /analyses/{analysis_id}/reruns` creates a new analysis linked by `parent_analysis_id`; it never mutates the original run.

## 4. State machines

### Dataset status

`uploading -> profiling -> ready`  
`uploading|profiling -> failed`

The create call returns after upload acceptance. `ready` means the profile is complete and analyses may start.

### Analysis status

`queued -> analyzing_question -> needs_clarification -> queued`  
`analyzing_question -> planning -> generating_code -> safety_check -> executing -> verifying -> packaging_evidence -> completed`  
Any non-terminal processing state may move to `repairing`, then back to `generating_code`.  
Any processing state may end in `refused`, `failed`, or `cancelled`.

Terminal statuses are `completed`, `refused`, `failed`, and `cancelled`. A `completed` analysis is not automatically verified; inspect `outcome` and `verification.status`. For the MVP, the backend should normally use:

- `outcome=answered` only when `verification.status=passed`;
- `outcome=partial` when safe, useful output exists but one or more declared requested outputs could not be verified;
- `outcome=refused` when a trustworthy answer cannot be supported.

## 5. Endpoint summary

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Process liveness; no dependency checks |
| GET | `/ready` | Database, storage, queue/worker, sandbox, and model readiness |
| GET | `/capabilities` | Effective formats, limits, result types, and contract version |
| POST | `/datasets` | Upload 1–10 files as one immutable dataset bundle |
| GET | `/datasets/{dataset_id}` | Dataset metadata and ingestion status |
| GET | `/datasets/{dataset_id}/profile` | Full profile after readiness |
| POST | `/analyses` | Start an asynchronous analysis |
| GET | `/analyses/{analysis_id}` | Status, progress, clarification, and terminal summary |
| GET | `/analyses/{analysis_id}/events` | SSE progress stream |
| POST | `/analyses/{analysis_id}/clarifications` | Supply an answer to the active clarification |
| GET | `/analyses/{analysis_id}/result` | Verified/partial/refused result envelope |
| GET | `/analyses/{analysis_id}/evidence` | Evidence manifest and generated code |
| POST | `/analyses/{analysis_id}/reruns` | Create an immutable rerun |
| POST | `/analyses/{analysis_id}/cancel` | Best-effort cancellation |

## 6. Core request and response examples

### Upload a dataset bundle

Request: `multipart/form-data`

- `files`: repeated binary field, required.
- `name`: optional string, 1–100 characters.

Response: `202 Accepted`

```json
{
  "id": "1c6555c4-38ea-4a66-b9b1-f8e28100b5e2",
  "name": "Q3 sales",
  "status": "profiling",
  "created_at": "2026-10-06T17:10:00Z",
  "expires_at": "2026-10-07T17:10:00Z",
  "files": [
    {
      "id": "c7ca20de-d3c4-481a-a111-ea950d57f0c5",
      "filename": "orders.csv",
      "media_type": "text/csv",
      "size_bytes": 18342,
      "sha256": "6f7d4d...64-lowercase-hex"
    }
  ],
  "table_count": null,
  "warnings": []
}
```

### Start an analysis

```http
POST /api/v1/analyses
Idempotency-Key: 56d6e076-60b8-43ba-8ef0-65fed29d83f0
Content-Type: application/json
```

```json
{
  "dataset_id": "1c6555c4-38ea-4a66-b9b1-f8e28100b5e2",
  "question": "Which region had the highest revenue growth and why?",
  "preferences": {
    "answer_format": "auto",
    "allow_explicit_assumptions": true
  }
}
```

Response: `202 Accepted`

```json
{
  "id": "d12a8b81-cf10-4724-8e83-57a1d3d8f82c",
  "dataset_id": "1c6555c4-38ea-4a66-b9b1-f8e28100b5e2",
  "parent_analysis_id": null,
  "status": "queued",
  "outcome": null,
  "stage": {"code": "queued", "label": "Queued", "progress_percent": 0},
  "created_at": "2026-10-06T17:12:00Z",
  "updated_at": "2026-10-06T17:12:00Z",
  "clarification": null,
  "failure": null
}
```

### Clarification

When `status=needs_clarification`, `clarification` is non-null:

```json
{
  "id": "clar_01",
  "question": "Which field should define revenue?",
  "reason": "Both gross_amount and net_amount are plausible.",
  "choices": [
    {"id": "gross", "label": "Gross amount", "description": "Before discounts and refunds"},
    {"id": "net", "label": "Net amount", "description": "After discounts and refunds"}
  ],
  "allow_free_text": true
}
```

Submit exactly one of `choice_id` or `text`:

```json
{"clarification_id":"clar_01","choice_id":"net"}
```

### Result envelope

```json
{
  "analysis_id": "d12a8b81-cf10-4724-8e83-57a1d3d8f82c",
  "outcome": "answered",
  "headline": "West had the highest revenue growth at 18.4%.",
  "narrative": "Net revenue increased from INR 2.61M to INR 3.09M...",
  "metrics": [
    {"id":"growth","label":"Revenue growth","value":18.4,"formatted_value":"18.4%","unit":"percent","evidence_refs":["claim_01"]}
  ],
  "tables": [],
  "charts": [],
  "assumptions": [
    {"id":"assumption_01","text":"Revenue means net_amount after refunds.","source":"user","impact":"Changes the numerator used in growth."}
  ],
  "warnings": [],
  "confidence": {
    "score": 0.91,
    "band": "high",
    "version": "confidence-v1",
    "summary": "High confidence: the question was resolved and all checks passed.",
    "factors": [
      {"name":"ambiguity","score":1.0,"weight":0.25,"explanation":"Revenue definition was confirmed by the user."},
      {"name":"data_quality","score":0.82,"weight":0.20,"explanation":"1.7% of region labels were normalized."},
      {"name":"execution","score":1.0,"weight":0.15,"explanation":"Execution completed without errors."},
      {"name":"verification","score":0.9,"weight":0.25,"explanation":"Key aggregates matched an independent query."},
      {"name":"reproducibility","score":1.0,"weight":0.15,"explanation":"Clean rerun output hash matched."}
    ]
  },
  "verification": {
    "status":"passed",
    "checks_passed":5,
    "checks_failed":0,
    "reproducible":true,
    "output_hash":"a3b2...64-lowercase-hex"
  },
  "evidence_url": "/api/v1/analyses/d12a8b81-cf10-4724-8e83-57a1d3d8f82c/evidence"
}
```

`headline` and `narrative` may be null for a refused/failed result. Every metric and table must include at least one `evidence_ref` when `outcome=answered`.

## 7. Confidence v1

The backend computes and returns the score. The frontend only renders it.

```text
score = 0.25*ambiguity
      + 0.20*data_quality
      + 0.15*execution
      + 0.25*verification
      + 0.15*reproducibility
```

Each factor is in `[0,1]`. Bands are `high >= 0.85`, `medium >= 0.65`, and `low < 0.65`.

Hard gates override the weighted score:

- safety check failure, execution failure, or failed reproducibility => no `answered` outcome;
- a failed key mathematical check => `partial` only if the failed claim is omitted, otherwise `refused`;
- unresolved material ambiguity => `needs_clarification` or `refused`, never a confident guess.

The formula is versioned so a later novelty may change scoring without breaking the UI.

## 8. Evidence contract

An evidence response contains:

- `plan`: the structured joins, filters, cleaning, calculations, outputs, and assumptions;
- `code_artifacts`: language, source code, SHA-256, entry point, and safety decision;
- `claims`: stable IDs referenced by result values;
- `sources`: file hash plus table, columns, row ranges/row IDs, or an aggregate query locator;
- `checks`: mechanical, independent-calculation, and clean-rerun checks with expected/actual summaries;
- `execution`: attempt count, duration, resource use, and canonical output hash.

Raw uploaded rows are not copied into normal logs. Evidence previews must be bounded and may be redacted by the backend.

## 9. Errors and HTTP rules

Every non-2xx JSON error uses the same envelope:

```json
{
  "error": {
    "code": "DATASET_NOT_READY",
    "message": "The dataset is still being profiled.",
    "request_id": "req_01J9Y...",
    "retryable": true,
    "details": {"current_status":"profiling"}
  }
}
```

| Status | Meaning |
|---|---|
| 400 | Malformed input or invalid state transition |
| 404 | Opaque ID not found or expired |
| 409 | Dataset not ready, stale clarification, or already terminal |
| 413 | File or bundle too large |
| 415 | Unsupported file/media type |
| 422 | Structurally valid request with invalid field values |
| 429 | Capacity/rate limit; respect `Retry-After` |
| 500 | Unexpected backend failure |
| 503 | Required dependency unavailable |

All responses include `X-Request-ID`. POST routes accept `Idempotency-Key`; repeating the same key and body for 24 hours returns the original resource, while reusing a key with a different body returns `409`.

## 10. SSE event contract

Response content type: `text/event-stream`. Each event has an `id`, `event`, and JSON `data`.

Supported event names:

- `snapshot`: full `Analysis` object, sent first;
- `stage.changed`: `{analysis_id, stage, occurred_at}`;
- `clarification.required`: `{analysis_id, clarification, occurred_at}`;
- `analysis.terminal`: `{analysis_id, status, outcome, occurred_at}`;
- `heartbeat`: `{occurred_at}`.

Clients reconnect with `Last-Event-ID`. If replay is unavailable, the server sends a fresh `snapshot`. Event delivery is at least once, so the frontend deduplicates by event `id`.

## 11. Frontend implementation rules

- Generate API types from `openapi.yaml`; do not maintain hand-written duplicates.
- Keep server state in the query cache, not in presentation components.
- Support polling even if SSE is used.
- Display backend warning text as plain text; never inject it as HTML.
- Label only `verification.status=passed` results as **Verified**.
- For `partial`, visibly identify omitted/unverified portions.
- Always expose assumptions, warnings, confidence factors, generated code, and evidence navigation.
- Use `formatted_value` for display and `value` for sorting/charting.
- Treat all IDs and enum values as opaque except those declared in OpenAPI.

## 12. Backend implementation rules

- Validate all requests and serialize all responses through the OpenAPI schemas.
- Preserve originals and compute hashes before parsing or normalization.
- Never send server paths, stack traces, prompts, secrets, or credentials to the client.
- Generated code receives read-only input paths, a writable temporary output directory, no network, and strict CPU/memory/time limits.
- Only allow approved imports and operations; the worker must not execute arbitrary shell commands.
- Verification must be independent of the answer prose and must record individual checks.
- Cap automated code repair at three attempts.
- A rerun creates a new immutable analysis and evidence package.
- Store UTC timestamps in RFC 3339 form.

## 13. Parallel build and acceptance fixtures

The frontend can begin with static mocks for these scenarios while the backend implements the same objects:

1. `happy_path`: two files, high confidence, verified scalar + table + chart.
2. `clarification`: ambiguous revenue columns and two choices.
3. `data_warnings`: mixed dates/currencies and a medium confidence answer.
4. `refused`: three failed verification/repair attempts with a precise reason.
5. `processing_failure`: worker unavailable with retryable failure details.

Integration is complete when both sides pass these checks:

- OpenAPI validation succeeds for every fixture.
- Upload accepts multiple files and rejects unsupported/oversized inputs predictably.
- Refreshing the browser during a job reconstructs state from `GET /analyses/{id}`.
- SSE disconnect falls back to polling without duplicate visible events.
- Clarification resumes the same analysis and a stale clarification is rejected.
- No unverified result is displayed as verified.
- Every reported metric resolves to at least one evidence claim.
- Rerun creates a different analysis ID and preserves the original.

## 14. Extension seams for later novelty

Later features should be added without changing the base workflow:

- New input connectors implement ingestion adapters but still create the same immutable dataset/profile objects.
- New result visualizations add chart `type` values or new artifact kinds; existing clients ignore unknown optional fields.
- Human approval, team sharing, and authentication wrap resources without changing their IDs.
- Domain-specific validators append verification checks and confidence factors; they do not bypass hard gates.
- Alternative models/planners remain behind the plan/code boundary.
- Streaming narrative, voice, anomaly agents, causal analysis, forecasting, and collaborative annotations are separate capabilities advertised through `/capabilities`.

Breaking changes require `/api/v2`. Additive optional fields and new capability flags remain in v1.
