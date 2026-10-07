# Local duplicate-datasheet environment

The simple upload-and-results frontend is at `http://127.0.0.1:8010/`.
Select CSV or Excel files, upload, inspect the profile, ask a question and answer
any table-selection clarification. Outputs show verification status, metrics,
tables and expandable evidence. Result JSON can be downloaded or rerun.

The test API uses `http://127.0.0.1:8010/api/v1` and its Swagger interface is at
`http://127.0.0.1:8010/api/v1/docs`. It has a separate SQLite database, uploaded
files and logs under `.test-env/`. It uses the deterministic planner and needs
no model credentials. All parsing and calculations use real Docker containers.
The v1 routes and response schemas remain unchanged.

## Docker on this Windows machine

Docker Desktop's installer crashed. Docker Engine was installed successfully
inside a dedicated WSL distribution named `HacknexTest`. This is the working
runtime; Docker Desktop is not required. For a fresh machine with WSL available:

```powershell
wsl --install Ubuntu-24.04 --name HacknexTest --no-launch --web-download
wsl -d HacknexTest -u root --exec bash '/mnt/c/Users/ravic/Documents/ChatGPT/Hacknex Hackathon/scripts/setup_wsl_docker.sh'
$env:DOCKER_WSL_DISTRO = 'HacknexTest'
.\.venv\Scripts\python.exe -B scripts/build_sandbox.py
.\.venv\Scripts\python.exe -B scripts/doctor.py
```

Adjust the repository path for another machine. The builder sends only the
Dockerfile, dependency lock and backend Python files. It avoids WSL extended
attribute errors encountered when building directly from the Windows checkout.
Native Docker installations should leave `DOCKER_WSL_DISTRO` unset.

## Start, test and stop

Run these commands from the repository root, with the virtual environment installed:

```powershell
$env:DOCKER_WSL_DISTRO = 'HacknexTest'
.\.venv\Scripts\python.exe -B scripts/make_test_data.py
.\.venv\Scripts\python.exe -B scripts/test_environment.py start
.\.venv\Scripts\python.exe -B scripts/test_environment.py status
# Wait for /api/v1/ready to return 200 before running the scenario.
.\.venv\Scripts\python.exe -B scripts/test_scenario.py
```

The supervisor keeps the API and worker running without visible terminal windows.
It also reads optional `.test-env/runtime.json` with `DOCKER_WSL_DISTRO`,
`DOCKER_EXECUTABLE` and `SANDBOX_IMAGE` overrides. This machine's override selects
`HacknexTest`. Logs are `.test-env/api.log`, `worker.log` and `supervisor.log`.
Stop before restarting after backend edits:

```powershell
.\.venv\Scripts\python.exe -B scripts/test_environment.py stop
```

The stop command signals the supervisor to stop only its own child processes.
It preserves the test database and reports. Uploaded artifacts expire after 24 hours.

## Fixtures and checks

`samples/sales.csv` and `sales-duplicate.csv` contain identical synthetic data.
`samples/sales-with-duplicate-sheet.xlsx` has two identical sheets. No real
datasheet is modified. Expected values are in `samples/expected.json`:
amount 5,000, units 50, profit 1,000, profit percentage 20%, and monthly growth
of 40% in West and -25% in East.

The HTTP scenario validates responses against the contract and checks duplicate
uploads, table-selection clarification, stale-answer rejection, multiple totals,
growth, percentages, evidence references, immutable reruns, matching output hashes,
SSE and Excel ingestion. Results are saved in `.test-env/report.json`.

```powershell
$env:RUN_DOCKER_TESTS = '1'
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider --basetemp=.test-env/checks-new
```

Choose a new temporary directory name for each run. Real Docker tests verify
effective isolation and reject deliberately weakened containers. The ordinary
suite uses an in-process test double for broader failure-path coverage. Live
OpenAI planning requires separate credentials and is not covered by these tests.
