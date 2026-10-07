# Host SureCount with Streamlit

Select repository rufusch/Proof-Carrying-Data-Analyst, branch main, and entrypoint streamlit_app.py in Streamlit Community Cloud. Select Python 3.12. Dependencies are installed from requirements.txt.

Remove any SURECOUNT_API_URL secret pointing at localhost or a placeholder URL, then reboot the app. With no backend URL configured, SureCount uses its built-in cloud demo runtime. No separate API, Docker daemon, or OpenAI key is required.

The cloud runtime reuses the same parsers, typed query validation, queue, SQL calculation, independent Decimal verification, skeptic checks, recovery forms, and AuditCode generation. It calls only fixed library functions; it never evaluates user-provided or model-generated Python. Agent 1 and Agent 2 remain independent calculation methods within the same app process.

## Cloud runtime limits

Docker/OS isolation and read-only mounts are not provided. The interface states this, and evidence records the actual properties. Use trusted test spreadsheets: parsing and calculations share the app process. Limits are 5 files, 10 MiB each, 25 MiB combined, 50,000 rows, 100 columns, and 10 tables. Uploads and the queue use temporary storage and disappear when the app restarts. A lock serializes processing within one process. This demo mode is not equivalent to the isolated Docker backend.

## Local run

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## Optional isolated backend

Set SURECOUNT_API_URL in Streamlit secrets or the environment to a real hosted API URL if you want to use the Docker-powered API and worker instead. A configured but unreachable backend produces a setup message rather than silently changing execution modes. Streamlit Cloud localhost is not your laptop.

The embedded runtime is tested locally; reboot your Streamlit Cloud app to deploy the repository update. A hosted deployment was not performed through your Streamlit account.
