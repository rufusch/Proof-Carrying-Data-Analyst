# SureCount — Proof-Carrying Financial Data Analyst

## Problem
HNX26PSI08: Proof-Carrying Data Analyst
Financial analysts spend time finding figures across spreadsheets, checking formulas, and proving where an answer came from. A fluent answer alone is not enough: incorrect filters, duplicate records, or missing exchange rates can change the result.

## Application and users

SureCount is a spreadsheet question-answering application built primarily for financial data analysts. Finance teams and reviewers can use it to explore revenue, assets, earnings, period comparisons, and growth while checking the calculation behind each answer.

## How it works

Upload one or more CSV or Excel files and ask a question in everyday language. SureCount maps the question to supported fields and calculations, asks for clarification when needed, and executes the calculation in an isolated environment. Agent 1 and Agent 2 independently check the result, while an extra reviewer tests selected data changes. Verified answers include source evidence, a confidence score, and expandable, copyable AuditCode. Missing inputs produce specific next steps instead of a guessed answer.

See the [Scope Note](docs/Scope%20Note.md) for MVP and novelty features, and the [Working example](docs/Working%20example.md) for a financial analysis walkthrough.

## Technical overview

To use the Streamlit interface, see [Streamlit hosting](docs/streamlit.md) and run `streamlit run streamlit_app.py`. It connects to the existing SureCount backend.

FastAPI implementation of [the v1 contract](docs/frontend-backend-contract.md).
The complete upload/question/result website is served at `/`, for example
`http://127.0.0.1:8010/` in the isolated test environment. It supports CSV, XLSX
and XLS and displays profiles, clarification choices, verification and plain-language calculation evidence. Its responsive interface uses the Tropical Heat palette: turquoise `#00CEC8`, cream `#FCEFC3`, peach `#FF9C5F`, and orange `#EB4203`. No frontend build step or external font/CDN is required.
The checked-in [OpenAPI document](docs/openapi.yaml) is used to validate incoming
JSON requests and outgoing resource/error envelopes at runtime. Swagger UI is at
`http://localhost:8000/api/v1/docs`; the integration base URL is
`http://localhost:8000/api/v1`.

## GitHub-ready source package

The [answer review extensions](docs/novelty-review.md) add verification by Agent 1 and Agent 2, an extra reviewer, measured sensitivity, and copyable AuditCode that reproduces the actual query using normalized source records. The evidence heatmap is removed. A missing exchange-rate refusal includes a parameterized Decimal function and a recovery form; supplied rates are recorded and verified through a new analysis. Sensitivity estimates never invent missing-value bounds, and material deduplication/date/join challenges withhold answer claims. The original API fields are retained, with optional review/recovery fields and a recovery endpoint documented in OpenAPI.

Run `python scripts/build_release.py` to create `dist/hacknex-github-ready.zip` and its SHA-256 checksum. Extract the `hacknex` folder, follow the setup below, and initialize a Git repository there when ready. The archive contains frontend, backend, locked dependencies, API contract, tests, synthetic examples, setup scripts, and CI. It excludes virtual environments, uploaded datasets, databases, logs, local credentials, Git history, and generated artifacts. Packaging does not commit or push anything.

The website supports file selection, drag-and-drop, clearing a pending selection, multiple-file upload, question suggestions, clarification, cancellation, result download, and rerunning calculations. Start with `samples/sales.csv`; try `How much did we make in West?` or `difference between amount and profit`.

## Run on Windows

For GitHub Codespaces, follow the [Codespaces commands](docs/codespaces.md). Financial test files are included in [datasets](datasets/README.md).

Requires Python 3.12+ and a Linux Docker engine (Docker Desktop or dedicated WSL). The API can
start without Docker, but `/ready` returns 503 and the worker cannot ingest or
execute jobs. There is intentionally no unsandboxed production fallback.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
Copy-Item .env.example .env
.\.venv\Scripts\python.exe scripts/build_sandbox.py
```

Run each process in its own terminal from this directory:

For the isolated duplicate-datasheet environment on port 8010, see
[test environment instructions](docs/test-environment.md). On this machine,
Docker Engine runs in the dedicated `HacknexTest` WSL distribution; set
`DOCKER_WSL_DISTRO=HacknexTest` when using it. The minimal-context image builder
avoids Windows/WSL metadata errors and excludes uploads, credentials and logs.

```powershell
.\scripts\start.ps1 api
```

```powershell
.\scripts\start.ps1 worker
```

On Linux/macOS, export the variables in `.env`, then use
`python -m uvicorn backend.api:app --host 127.0.0.1 --port 8000` and
`python -m backend.worker`. API and worker must share the same database and
absolute `DATA_DIR`; Docker must be able to bind-mount that directory.

The default database is SQLite for local development. Set
`DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/analyst`
for PostgreSQL. Versioned additive schema migrations run on startup. The durable database
queue removes the need to operate Redis for this first implementation. Keep
the API and worker on one host with shared storage. Existing queues are upgraded
in place; a newer unknown schema version is rejected.

## Planner configuration

Annual financial questions recognize numeric `Year`, `Fiscal Year`, or `Reporting Year` columns. `What is the difference between Total Assets between 2021 and 2022?` computes the 2022 total minus the 2021 total. `Rate of growth for Total Assets between 2021 and 2022` computes that change divided by the 2021 total, times 100. Without specified years, a growth-rate question asks for confirmation of the two latest indexed years and accepts a free-text pair such as `2020 to 2021`. Missing periods are refused. Explicit `($B)`, `($M)`, and `($K)` headers supply display units for period comparisons without changing numeric scale.

The local dataset parser builds a private, bounded vocabulary from every parsed table: header words and up to 256 distinct short category/date/boolean values per field. Vocabulary construction reads beyond the ten-row preview. Questions can select records without naming the category column (`How much did we make in West?`), use business aliases (`total money made`), or specify numeric conditions (`total sales where revenue is above 100`). Approximate header/value matches and values appearing in multiple fields require confirmation. Existing datasets acquire this index from their hash-checked normalized artifact on their next question. The public profile/result contract is unchanged; category values stay local to the deterministic planner.

The vocabulary supports grounded calculations, not unrestricted questions or invented business definitions. Missing categories, unsupported clauses, causal explanations and forecasts do not become partial answers. Long free-text cells and values beyond the bounded category index are not indexed.

The default `PLANNER=deterministic` needs no model credentials. It combines the
exact query grammar with a trained local intent model and semantic schema matching.
Questions can use synonyms, spaces, camel case or different plural forms:

* `How much revenue did we make?` (can map to `OrderAmount` or `Total_Sales`)
* `What is our average sales by region?`
* `How many orders do we have?`
* `Could you show me total costs?`
* `What is the revenue made in the last quarter?`
* `What is the total revenue?`
* `Revenue?` (defaults to a total with reduced interpretation confidence)

Ambiguous fields such as gross/net sales require confirmation. Non-literal
mappings appear in evidence assumptions. Disabling explicit assumptions requires
confirmation before using them. Entity counts mean non-null values, not distinct
entities. Several CSV files remain separate tables; matching files require table
selection and are never silently concatenated. The following explicit syntax is
also supported:

* `sum amount`
* `average amount by region`
* `count rows`
* `count customer_id by region`
* `maximum amount`
* `sum amount and mean amount and count rows by region`
* `ratio of amount to units`
* `percentage of profit to amount`
* `growth of amount from 2025-01 to 2025-02 using day by region`
* `difference between revenue and cost` (total revenue minus total cost)
* `subtract cost from revenue by region`
* `change in revenue from Q2 2025 to Q3 2025 using Quarter`
* `difference in revenue between the last quarter and the previous quarter`
* `percentage change in revenue from the previous quarter to the latest quarter`

Differences preserve their sign. Column comparisons subtract totals over the same complete records; period comparisons subtract the earlier period total from the later period total. Missing periods and incompatible currency units are refused. The result explains the operands and subtraction order. These calculations use the existing result/evidence API contract.

If several tables match, the same analysis asks which one to use. Unsupported
questions are refused rather than partially interpreted. See
[local model training and scope](docs/natural-language-planner.md).

For natural-language planning, configure both API and worker:

```dotenv
PLANNER=openai
OPENAI_MODEL=<a model available to your account with structured outputs>
OPENAI_API_KEY=<your server-side key>
```

The adapter uses the [Responses API structured-output parser](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses).
Only the question, clarification history and schema/quality metadata go to the
model; sample rows and sample cell values are excluded. Table and column names
can still contain sensitive information. Keys stay in the worker environment
and are not forwarded into execution containers. Model availability is checked
by the worker and reflected in readiness.

The current engine supports **up to eight basic aggregates** (`count`, `sum`, `mean`, `min`,
`max`) sharing optional grouping, AND filters, and one explicitly confirmed many-to-one
inner join. It rejects joins that multiply rows or omit unmatched keys. It
also supports a ratio or percentage of column totals, or period-over-period
growth with explicit baseline/current calendar windows. Advanced calculations
return their component totals and independently checked derived values. They
cannot be mixed with additional aggregates in one query. Growth requires a
positive baseline, complete amounts in both periods, and ISO dates or naive
midnight timestamps. Missing periods are never silently replaced with zero.
Zero denominators, incomplete ratio inputs and ambiguous dates cause refusal.
Causal explanations, forecasts and arbitrary Python remain unsupported. These
limits are advertised by `/capabilities`. A model cannot expand the allowlist.

## Frontend integration

1. Read `/capabilities` and `/ready`.
2. POST `/datasets` with repeated multipart `files` fields and optional `name`.
3. Poll the returned dataset ID until ready, then fetch its `/profile`.
4. POST `/analyses` with `dataset_id` and `question`.
5. Follow `/analyses/{id}/events`, with GET `/analyses/{id}` as polling fallback.
6. Answer the active clarification using exactly one of `choice_id` or `text`.
7. Fetch `/result` and `/evidence` when terminal. Only `verification.status=passed`
   is verified. Refusals before execution have no evidence package.

All POST routes accept `Idempotency-Key` (8–128 characters). Keys are scoped to
the exact route for 24 hours. Replays return the **original accepted response**;
poll GET for current status. A changed request under the same key returns 409.
Upload fingerprints cover names, order, sizes and SHA-256 content hashes.

SSE begins with a snapshot, replays durable events after a recognized
`Last-Event-ID`, and emits heartbeats. Unknown/expired IDs trigger a fresh
snapshot. Snapshot and heartbeat IDs are ephemeral; durable event IDs are
integers encoded as strings. Clients should tolerate at-least-once delivery.

By default CORS allows `http://localhost:5173`. Configure explicit
`CORS_ORIGINS` for your frontend. Authentication is deferred per the contract;
keep this unauthenticated MVP on a trusted network. Put an HTTPS reverse proxy
in front of it for any non-local deployment. Do not expose the Docker daemon.

## Verification and evidence

* Originals are hashed before parsing and retained under opaque UUIDs.
* CSV supports UTF-8/UTF-8 BOM and comma delimiters; malformed row widths and
  duplicate headers fail. Leading-zero and oversized integer identifiers stay
  text. Decimal values that lose decimal digits or underflow during conversion
  are retained as text and flagged. Numeric calculations use DuckDB's numeric representation; floating
  comparisons use relative tolerance `1e-10` and absolute tolerance `1e-9`.
* XLSX uses bounded decompression and no formula evaluation. Completely empty
  formatting columns are ignored. Unreadable/formula-containing sheets are
  excluded with explicit file/sheet warnings; valid sheets remain usable. If no
  sheet is usable, ingestion fails with the reasons and suggested corrections.
  Populated columns without headers are never silently removed. XLS uses stored
  values and carries a freshness warning.
* Nulls are explicit, duplicates are retained, and no implicit currency/unit
  conversion or date-order normalization occurs.
* A consistent currency marker and valid thousands separators can be removed
  losslessly for arithmetic, with a profile warning and retained display unit.
  Mixed currencies, ambiguous number formats and precision loss remain text.
  No exchange-rate conversion or million/billion scaling is inferred.
* Last/latest quarter means the latest calendar quarter present in the uploaded
  reporting dates. Its exact window appears in assumptions and evidence filters.
  Previous quarter means the preceding calendar quarter. Vague financial metric
  questions can default to a sum; disabling assumptions requires confirmation.
* Parsing and execution run in fresh containers with no network, read-only
  inputs/root filesystem, dropped capabilities, non-root user, 768 MiB memory,
  one CPU, a 60-second CPU limit, a 90-second wall timeout and a 64 MiB scratch
  directory. Only the fixed typed-query compiler can produce executable SQL.
  Each container checks its effective isolation before parsing or execution;
  inadequate isolation fails closed.
* Every key result is recalculated through a separate Python/Decimal algorithm
  that does not reuse generated SQL or DuckDB aggregation.
* A second fresh container must reproduce the canonical output hash.
* At most three execution attempts are permitted. Unverified claims are omitted;
  exhausted verification yields refusal, not a fabricated answer.
* Evidence records source hashes, exact SQL and hash, assumptions, individual
  checks, attempt history, measured CPU/memory/duration and canonical output hash.

Jobs, state, events, clarification history and idempotency records persist in
the database. A lease and heartbeat allow recovery after a worker crash.
Cancellation is best effort: an already running container may finish, but its
output cannot overwrite a cancelled resource. Reruns create new IDs and preserve
the original evidence. Dataset-linked records expire together after 24 hours;
the worker cleans expired artifacts every minute. Stop the worker and physical
cleanup stops too, though API access still expires.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The standard suite exercises the real parsing, SQL and independent calculation
code using a **test-only in-process sandbox double**. It is not evidence that
Docker isolation works on the current host. To test the real boundary:

```powershell
$env:RUN_DOCKER_TESTS = '1'
.\.venv\Scripts\python.exe -m pytest -m docker -q
```

The live model integration requires configured credentials and is not called by
the offline suite. The implementation has no mock-result mode in the API or
worker. See [implementation notes](docs/backend-implementation.md) for limits
and deployment checks.

Five frontend acceptance fixtures are checked in under `docs/fixtures`: happy
path, clarification, data warnings, refusal after three failed checks, and
retryable processing failure. They are schema-validated test artifacts, not
production evidence. Regenerate with `python scripts/export_fixtures.py`.
