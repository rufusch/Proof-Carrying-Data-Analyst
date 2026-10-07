# Backend implementation notes

## Module boundaries

| Module | Responsibility |
|---|---|
| `backend/api.py` | HTTP validation, upload streaming, request IDs, errors, SSE and all v1 routes |
| `backend/contract.py` | Runtime JSON Schema validation against the supplied OpenAPI |
| `backend/store.py` | PostgreSQL/SQLite persistence, serialized control-plane transactions, durable job leases |
| `backend/service.py` | Resource creation, state changes and terminal event publication |
| `backend/worker.py` | Ingestion/analysis orchestration, repair cap, cancellation checks and retention |
| `backend/ingest.py` | CSV/XLSX/XLS parsing, quality profile and conservative join suggestions |
| `backend/planner.py` | Deterministic planner and optional OpenAI structured plan adapter |
| `backend/engine.py` | Typed query validation, SQL compilation and independent Decimal recalculation |
| `backend/sandbox.py` | Resource-limited Docker launch with no shell or inherited credentials |
| `backend/sandbox_entry.py` | Fixed sandbox entry point |
| `backend/results.py` | Claims, evidence, confidence and hard verification gates |

## Deliberate initial constraints

The contract permits a precise refusal when a trustworthy answer cannot be
produced. This implementation uses that path for unsupported calculations and
ambiguous inputs. It is an initial bounded backend, not an unrestricted data
science agent. It never answers only the convenient part of a larger request.

The existing blueprint named PostgreSQL, Redis/RQ, object storage and Alembic.
This first implementation supports PostgreSQL but uses a transactional database
job queue and local immutable source artifacts to reduce required services.
Redis/RQ, remote object storage and horizontally distributed workers are not
implemented. A common filesystem is required. An additive versioned migration
preserves existing jobs and adds FIFO enqueue times. Control-plane transactions
serialize initialization, and a resource cannot have two active job leases.

The execution allowlist is intentionally smaller than the long-term product
scope. Explicit period growth, ratios/percentages of totals, and up to eight
basic aggregates are supported. Causal explanations remain refused. The backend
returns scalar metrics or grouped tables/charts; it does not emit `partial`:
all requested calculations in an accepted plan must verify together. It supports explicit filters and a single confirmed
many-to-one inner join through the model planner. SQL is rerunnable against
tables named by the profile IDs; original source names and hashes are included
in source locators. Normalized JSON artifacts are internal, not download routes.

The OpenAPI definition does not include an artifact-download endpoint or an
execution query-schema endpoint; neither is added. The structured execution DSL
is internal. Evidence exposes executable SQL and its source bindings.

## Operational limits

* Upload: 10 files, 50 MiB/file, 200 MiB/bundle. Request envelope has 1 MiB overhead.
* Parsed bundle: 500,000 rows, 40 tables, 200 columns/table, 10,000 characters/cell.
* XLSX: 256 MiB expanded archive limit; suspicious compression ratios rejected.
* Result: maximum 500 groups. More groups cause refusal, not silent truncation.
* Queue: 100 active jobs; overflow returns 429 and `Retry-After: 5`.
* Clarification: maximum five rounds; verification/execution: maximum three attempts.
* Source/evidence retention: 24 hours, including analyses and reruns linked to a dataset.

Readiness reports database, storage, durable queue, worker heartbeat, sandbox
probe and model readiness separately. Liveness does not query dependencies.
The Docker runner never pulls images while processing a job; build the tagged
image before starting the worker. Docker access is privileged infrastructure;
give it only to the worker host, not the API-facing network.

SSE state changes and resources are committed in the same database transaction.
API cancellation and worker publication also serialize through this transaction
boundary. PostgreSQL uses a transaction advisory lock; SQLite uses
`BEGIN IMMEDIATE`. Heavy parsing, model calls and calculations occur outside
transactions. This favors predictable correctness over high throughput.

## Before a real deployment

Run the opt-in Docker integration test on the actual deployment host. Run an
end-to-end upload and model-planned analysis with the selected model account.
Verify PostgreSQL connectivity and shared bind-mount paths. Configure TLS, CORS,
disk quotas and retention monitoring. Add authentication before use outside a
trusted MVP environment. Keep credentials outside tracked files. Run the API
and worker under a process supervisor; `/ready` becomes non-ready if the worker
heartbeat stops.

Raw uploaded rows are not logged. Unexpected library exceptions are replaced
with generic failure messages so paths, prompts and secrets are not returned.
This intentionally limits diagnostics in the current version; use request IDs,
resource status and fixed error codes for integration debugging.
# Workbook layout and error reporting

Excel formatting can extend a sheet beyond its populated table. The parser now
ignores columns only when their header and every data cell are genuinely empty.
It preserves populated cells and refuses unnamed populated columns rather than
guessing their meaning. It does not evaluate formulas or trust cached XLSX formula
results. Invalid sheets are explicitly excluded with `SHEET_EXCLUDED` warnings;
valid sheets remain separate tables. If every sheet is excluded, the dataset
fails with file/sheet-specific reasons. Exclusion warnings appear in the profile
and all analysis results so a selected-table answer does not imply whole-workbook
coverage. The frontend displays `Dataset.failure.message` instead of a generic
upload error.

Question refusals name missing concepts, available columns, or nonnumeric fields
and explain corrective steps. A consistent currency marker with unambiguous
thousands separators is now normalized losslessly, recorded in warnings and
displayed in metric units. Mixed currencies and ambiguous text remain text;
no exchange-rate or magnitude conversion is inferred.
