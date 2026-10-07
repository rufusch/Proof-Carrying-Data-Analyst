# SureCount — Proof-Carrying Financial Data Analyst

## What the project does

**Problem statement: HNX26PSI08 — Proof-Carrying Data Analyst.** Financial analysts spend time finding spreadsheet figures, checking formulas, and proving where an answer came from. Incorrect filters, duplicates, or missing exchange rates can change the result.

SureCount is a spreadsheet question-answering application primarily for financial data analysts, finance teams, and reviewers. Upload CSV or Excel files and ask about revenue, assets, earnings, totals, differences, or growth. Results include units, source evidence, confidence, and expandable **AuditCode** that can be copied and rerun.

The workflow is **upload → profile → interpret/clarify → calculate → independently verify → review → answer**. Agent 1 uses DuckDB SQL; Agent 2 recomputes with Python Decimal arithmetic. An extra reviewer tests selected changes such as duplicate removal. Material disagreements withhold the answer. Missing exchange rates produce specific input requirements and a parameterized recovery form. Sensitivity explains which tested changes affect the result.

## Technologies, libraries, and models

| Component | Technology and purpose |
| --- | --- |
| Language | Python 3.12+ |
| Interfaces | HTML/CSS/JavaScript website; Streamlit alternative with requests for external API calls |
| API and validation | FastAPI, Uvicorn, Pydantic, JSON Schema/jsonschema, PyYAML and OpenAPI |
| Spreadsheet parsing | pandas, openpyxl for XLSX, xlrd for XLS; CSV parsing and bounded upload validation |
| Calculations | DuckDB SQL and independently implemented Python Decimal arithmetic |
| Storage and queue | SQLAlchemy and SQLite; optional PostgreSQL through psycopg |
| Default intent model | Local supervised TF-IDF nearest-example classifier, packaged in backend/intent_model.json; semantic aliases and a bounded dataset vocabulary ground field selection |
| Optional model planner | OpenAI SDK/Responses structured-output adapter; user supplies an available model name and API key |
| Review | Deterministic bounded skeptic, source hashes, identical-unit/filter checks and repeated execution |
| Isolation | Docker for the API/worker deployment; explicit fixed-library execution for the Streamlit cloud demo |
| Testing | pytest, httpx/TestClient, Streamlit AppTest; optional Playwright browser checks |

The default model needs no API key. Uploaded financial records are parsed and indexed, **not used to train a new language model**. The reviewer is deterministic, not a second trained LLM. See [model training and scope](docs/natural-language-planner.md).

## Install dependencies

Clone the repository and work from its root:

```bash
git clone https://github.com/rufusch/Proof-Carrying-Data-Analyst.git
cd Proof-Carrying-Data-Analyst
python -m venv .venv
```

Activate the environment:

```bash
# Linux, macOS or Codespaces
source .venv/bin/activate
```

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

Choose the dependencies for your deployment:

```bash
# Streamlit, including the built-in cloud runtime and backend libraries
python -m pip install -r requirements.txt

# Or: API/worker only, using pinned backend dependencies
python -m pip install -r requirements.lock
```

If PowerShell activation is restricted, use `.\.venv\Scripts\python.exe` in place of `python`. The HTML website requires no Node build or external font/CDN.

## Configure and run

### Streamlit: easiest local or cloud demo

```bash
python -m streamlit run streamlit_app.py
```

Open the local URL shown by Streamlit, normally http://localhost:8501. With **no SURECOUNT_API_URL configured**, the application runs its built-in backend, queue and verified calculations; no Docker daemon or external API is required. Click **Refresh status** when needed.

For **Streamlit Community Cloud**, select this repository, branch `main`, entrypoint `streamlit_app.py`, and Python **3.12**. Remove any localhost/placeholder `SURECOUNT_API_URL` secret and reboot the app. Dependencies are installed from `requirements.txt`. To use a separately hosted isolated backend instead, configure:

```toml
# Streamlit app secrets; replace with a real reachable URL
SURECOUNT_API_URL = "https://your-hosted-surecount-backend.example"
```

The cloud demo uses fixed library code and retains SQL/Decimal verification, skeptic review and AuditCode. It **does not provide Docker isolation or read-only filesystem mounts**. Use trusted test files. Its temporary storage disappears on restart. Limits: 5 files, 10 MiB per file, 25 MiB total, 50,000 rows, 100 columns and 10 tables. [Full Streamlit instructions](docs/streamlit.md).

### Docker-isolated API and HTML website

Prerequisites: Python 3.12+, installed backend dependencies, and a running Linux Docker engine. Docker Desktop or a dedicated WSL Docker engine can be used on Windows.

Copy `.env.example` to `.env` and configure both the API and worker consistently:

| Setting | Meaning/default |
| --- | --- |
| DATA_DIR | Shared normalized records and uploads; default ./data |
| DATABASE_URL | Shared SQLite queue/state by default; optional PostgreSQL URL |
| SANDBOX_IMAGE | proof-analyst-sandbox:local |
| PLANNER | deterministic; no credentials required |
| DOCKER_WSL_DISTRO | Leave empty for native Docker; set only to an existing Windows WSL Docker distribution |
| CORS_ORIGINS | Allowed separate browser frontend origins; the bundled website is same-origin |
| OPENAI_MODEL / OPENAI_API_KEY | Required only when PLANNER=openai |

Never commit `.env` or `.streamlit/secrets.toml`. Optional OpenAI planning sends the question, clarification history and schema metadata, excluding sample rows; headers can still contain sensitive information.

**Linux/macOS/Codespaces**, first terminal:

```bash
cp .env.example .env
set -a
source .env
set +a
docker info
python scripts/build_sandbox.py
python -m backend.worker
```

Second terminal, from the same repository with the same virtual environment:

```bash
source .venv/bin/activate
set -a
source .env
set +a
python -m uvicorn backend.api:app --host 0.0.0.0 --port 8000
```

**Windows PowerShell**, build the image, then use separate terminals:

```powershell
Copy-Item .env.example .env
# If necessary, export configured Docker variables before building.
python scripts/build_sandbox.py
.\scripts\start.ps1 api
```

```powershell
.\scripts\start.ps1 worker
```

Open http://localhost:8000 for the website, `/api/v1/docs` for Swagger, or `/api/v1/ready` for readiness. Both processes must share the database and absolute data directory, which Docker must be able to mount. This deployment fails closed without Docker; the Streamlit library runtime is a separate, explicitly disclosed mode. For Codespaces, open forwarded port **8000**. See [Codespaces setup](docs/codespaces.md) or [the isolated port-8010 test environment](docs/test-environment.md).

## Reproduce the demonstrated results

The supplied files are checked in under [datasets](datasets/README.md). Screenshots are in [Working example](docs/Working%20example.md).

### Screenshot walkthrough

1. Start either interface using the instructions above.
2. Upload `datasets/McDonalds_Financial_Statements.csv` by itself.
3. Ask **What is the growth in revenue between 2002 and 2022?**
4. If asked which field revenue means, choose **Revenue ($B)**, then continue. In Streamlit, refresh status if needed.
5. Expect baseline **15.4 billion dollars**, comparison **23.18 billion dollars**, change **7.78 billion dollars**, and growth **50.5195%** after rounding.
6. Check Agent 1 and Agent 2 agreement, review sensitivity, and expand AuditCode to copy the standalone script.

The calculation is `(23.18 - 15.4) / 15.4 * 100`. AuditCode contains normalized source records and the actual query; install DuckDB/pandas as instructed in its header and run `python audit_code.py`. Copying AuditCode also copies those records. Confidence is an interpretation heuristic; the screenshot's 98% should not be treated as a fixed expected value or calibrated probability.

### Automated reproduction: no server or Docker required

After installing dependencies, run:

```bash
python scripts/reproduce_demo.py
```

The script uploads the checked-in CSV through the embedded API, handles the Revenue field clarification, runs the full verification pipeline, and compares the result against an independent Decimal calculation from the original CSV cells. Expected output:

```text
Baseline revenue (2002): 15.4 billion dollars
Comparison revenue (2022): 23.18 billion dollars
Change: 7.78 billion dollars
Growth: 50.5195%
PASS: source-cell calculation, Agent 1, Agent 2 and extra reviewer agree.
```

For the supplied Tesla workbook, upload `datasets/Tesla_Financial_Report.xlsx` separately. Ask **What is the revenue made in the last quarter?** and **What is the total revenue?**. Expected numeric results are **28,095** and **503,253**, respectively, in the source Revenue field's units. Latest quarter means the latest quarter present in that workbook, not today's calendar quarter.

To exercise a running Docker-backed test environment on port **8010**, use:

```bash
python scripts/test_mcd_http.py datasets/McDonalds_Financial_Statements.csv
python scripts/test_tesla_http.py datasets/Tesla_Financial_Report.xlsx
```

These live scripts verify annual asset comparisons and Tesla totals against original cells. They require the port-8010 API and worker described in the test-environment guide.

## Validation and limits

```bash
python -m pytest -q
```

The standard suite checks parsing, contracts, calculations, recovery, review gates and the embedded runtime. Streamlit UI checks run when Streamlit is installed. Docker-only checks are skipped unless enabled:

```bash
# Linux/macOS, after building the sandbox image
RUN_DOCKER_TESTS=1 python -m pytest -m docker -q
```

```powershell
# Windows
$env:RUN_DOCKER_TESTS='1'
python -m pytest -m docker -q
```

Passing library tests does not establish Docker isolation. SQL/Decimal outputs are compared at relative tolerance 1e-10 and absolute tolerance 1e-9. Docker mode uses fresh isolated executions; cloud mode repeats fixed-library execution within the app process.

SureCount supports defined spreadsheet operations rather than every financial question, causal explanation or forecast. Files remain separate tables; ambiguous fields require confirmation. Currency recovery applies one explicitly supplied rate to an optionally filtered/grouped total. Sensitivity reports measured scenarios and labeled assumptions, never invented missing-value bounds. The skeptic covers selected alternatives, not all possible interpretations. The unauthenticated API is an MVP intended for trusted environments.

## Supporting files and packaging

- [Scope Note: MVP and novelty features](docs/Scope%20Note.md)
- [Working example with screenshots](docs/Working%20example.md)
- [Data Pipeline & Tech Stacks presentation](docs/Data%20Pipeline%20%26%20Tech%20Stacks.pptx)
- [API specification](docs/openapi.yaml), [original contract](docs/frontend-backend-contract.md), and [review extensions](docs/novelty-review.md)

Run `python scripts/build_release.py` to generate `dist/hacknex-github-ready.zip` and its SHA-256 checksum. The archive includes frontend/backend source, dependencies, docs, supplied test datasets, presentation, tests and CI. It excludes runtime uploads, databases, logs, virtual environments and credentials. Packaging itself does not push to GitHub.
