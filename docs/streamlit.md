# Host SureCount with Streamlit

`streamlit_app.py` is a Streamlit interface to the existing SureCount API. It supports upload, preview, questions, clarification, results, verification, sensitivity, currency recovery, download, cancellation, and collapsed AuditCode with Streamlit's code-copy control.

## Local run

Keep the existing API and Docker worker running, then run:

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

The default backend is `http://127.0.0.1:8010`. Set `SURECOUNT_API_URL` to use a different backend, such as `http://127.0.0.1:8000` in Codespaces.

## Streamlit Community Cloud

Select this repository, branch `main`, and entrypoint `streamlit_app.py`. Set this secret in the app's advanced settings:

```toml
SURECOUNT_API_URL = "https://your-hosted-surecount-backend.example"
```

Replace the example with a reachable SureCount backend running its API, database, and Docker worker. Deploying this Streamlit file alone does not start that backend, and a cloud app cannot reach the localhost backend on your laptop. Backend requests are made by the Streamlit server, so iframe embedding and cross-origin browser API configuration are unnecessary. The interface shows a setup message when the backend is unavailable.

The Streamlit frontend uses `requirements.txt`; backend development continues to use `requirements.lock`. Refresh status checks progress without starting duplicate analyses. This frontend is not a pixel-identical copy of the HTML website. A hosted Streamlit deployment has not been performed.
