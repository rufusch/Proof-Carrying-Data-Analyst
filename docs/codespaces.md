# Run SureCount in GitHub Codespaces

Create a Codespace from this repository. The included dev container provides Python 3.12, Docker-in-Docker, and port 8000 forwarding. If a Codespace already exists, pull the latest changes and run **Codespaces: Rebuild Container** first.

In the repository's terminal, run:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.lock
docker info
python scripts/build_sandbox.py
python -m backend.worker
```

Leave the worker running. Open a second terminal in the same repository and run:

```bash
source .venv/bin/activate
python -m uvicorn backend.api:app --host 0.0.0.0 --port 8000
```

In **Ports**, open port **8000** in your browser. Use the Codespaces forwarded URL, rather than your own machine's localhost. Keep the port private. Download a file from the repository's `datasets` folder and select it in the website's upload control.

Both processes use the same default local SQLite database and absolute data directory. No OpenAI API key is needed for the default planner. Stop each process with Ctrl+C. A working Docker daemon is required for uploads and calculations.

The configuration and commands are provided for Codespaces; a hosted Codespace was not launched during local verification.
