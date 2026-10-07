"""Streamlit frontend for SureCount's existing verified-analysis API."""
import json
import os
import uuid

import requests
import streamlit as st


st.set_page_config(page_title="SureCount", page_icon="✳", layout="wide")
st.markdown("""<style>
div.stButton>button{background:#EB4203;color:white;border:0}
</style>""", unsafe_allow_html=True)
st.title("SureCount")
st.subheader("Your numbers. A clearer story.")
st.write("Upload financial spreadsheets, ask a question, and inspect the verified calculation.")

try:
    configured = st.secrets.get("SURECOUNT_API_URL", "")
except (FileNotFoundError, st.errors.StreamlitSecretNotFoundError):
    configured = ""
base = (configured or os.getenv("SURECOUNT_API_URL", "")).rstrip("/")
if base and not base.endswith("/api/v1"):
    base += "/api/v1"
if st.session_state.get("backend") != base:
    st.session_state.clear()
    st.session_state.backend = base


@st.cache_resource
def embedded_backend():
    from backend.streamlit_runtime import EmbeddedBackend
    return EmbeddedBackend()


if not base:
    st.caption("Cloud demo mode: calculations run within this app using fixed library code. Docker isolation is not enabled. Uploads are temporary and disappear when the app restarts.")


def api(path, method="GET", **kwargs):
    headers = {"Idempotency-Key": str(uuid.uuid4())} if method == "POST" else {}
    response = (requests.request(method, base + path, headers=headers, timeout=60, **kwargs)
                if base else embedded_backend().request(method, path, headers=headers, **kwargs))
    try:
        body = response.json()
    except ValueError:
        raise RuntimeError("The configured backend did not return a valid API response.") from None
    if not 200 <= response.status_code < 300:
        raise RuntimeError(body.get("error", {}).get("message", "Backend request failed."))
    return body


def analysis_created(body):
    st.session_state.analysis_id = body["id"]
    st.rerun()


try:
    api("/ready")
except (requests.RequestException, RuntimeError) as exc:
    st.info("The verified-analysis backend is unavailable. Start the SureCount API and Docker-powered worker, or set SURECOUNT_API_URL in Streamlit secrets to your hosted backend URL.")
    st.caption(str(exc))
    st.stop()

try:
    left, right = st.columns(2)
    with left:
        st.header("1. Bring your data")
        files = st.file_uploader("CSV or Excel files", type=["csv", "xlsx", "xls"], accept_multiple_files=True)
        if st.button("Upload & explore", disabled=not files):
            uploaded = api("/datasets", "POST", files=[("files", (f.name, f.getvalue(), f.type or "application/octet-stream")) for f in files])
            st.session_state.dataset_id = uploaded["id"]
            st.session_state.pop("analysis_id", None)
            st.rerun()
        dataset_id = st.session_state.get("dataset_id")
        ready = False
        if dataset_id:
            dataset = api("/datasets/" + dataset_id)
            ready = dataset["status"] == "ready"
            if ready:
                st.success("Dataset ready")
                profile = api("/datasets/" + dataset_id + "/profile")
                for table in profile["tables"]:
                    st.write(table["name"])
                    st.dataframe(table["sample_rows"], width="stretch")
            elif dataset["status"] == "failed":
                st.error(dataset.get("failure", {}).get("message", "Upload processing failed."))
            else:
                st.info("Processing your files. Click Refresh status in a moment.")
    with right:
        st.header("2. Ask something interesting")
        with st.form("question"):
            question = st.text_area("What would you like to know?", placeholder="What is the growth in revenue between 2002 and 2022?")
            if st.form_submit_button("Find my answer", disabled=not ready):
                if question.strip():
                    st.session_state.last_question = question.strip()
                    analysis_created(api("/analyses", "POST", json={"dataset_id": dataset_id, "question": question.strip()}))
                else:
                    st.error("Enter a question first.")
    if st.button("Refresh status"):
        st.rerun()
    analysis_id = st.session_state.get("analysis_id")
    if analysis_id:
        analysis = api("/analyses/" + analysis_id)
        state = analysis["status"]
        if state == "needs_clarification":
            item = analysis["clarification"]
            with st.form("clarify"):
                st.write(item["question"])
                choices = item["choices"]
                chosen = st.selectbox("Choose an interpretation", [c["id"] for c in choices], format_func=lambda x: next(c["label"] for c in choices if c["id"] == x)) if choices else None
                text = st.text_input("Or enter your own clarification") if item["allow_free_text"] else ""
                if st.form_submit_button("Continue"):
                    answer = {"clarification_id": item["id"], **({"text": text} if text.strip() else {"choice_id": chosen})}
                    api(f"/analyses/{analysis_id}/clarifications", "POST", json=answer)
                    st.rerun()
        elif state in {"completed", "refused", "failed", "cancelled"}:
            result = api(f"/analyses/{analysis_id}/result")
            st.header("3. Your answer")
            st.subheader(result["headline"] or result["outcome"])
            st.caption(f"Verification: {result['verification']['status']} · Confidence: {result['confidence']['score']:.0%}")
            for metric in result["metrics"]:
                st.metric(metric["label"], str(metric["value"]) + (" " + metric["unit"] if metric.get("unit") else ""))
            for table in result["tables"]:
                st.dataframe(table["rows"], width="stretch")
            if result["outcome"] == "answered":
                evidence = api(f"/analyses/{analysis_id}/evidence")
                with st.expander("AuditCode — view calculation code", expanded=False):
                    artifact = next((a for a in evidence["code_artifacts"] if a["id"] == "audit_python"), None)
                    if artifact:
                        st.code(artifact["source"], language="python")
                        st.download_button("Download AuditCode", artifact["source"], "audit_code.py")
                    else:
                        st.write(result["narrative"])
            else:
                st.write(result["narrative"])
            review = result.get("review")
            if review:
                st.subheader("Two independent checks")
                st.write("Agent 1 and Agent 2 got the same result." if review["two_analyst"]["status"] == "passed" else "The two checks have not verified an answer.")
                st.write("The extra reviewer found no conflicting answer in its checks." if review["skeptic"]["status"] == "passed" else "The extra review needs attention or more information.")
                st.subheader("What would change this?")
                for component in review["sensitivity"]:
                    impacts = [i for i in component["impacts"] if i["delta"] is not None and abs(i["delta"]) > 1e-9]
                    if impacts:
                        for item in impacts:
                            st.write(f"{item['scenario']}: changes {component['key']} by {item['delta']:.8g}" + (f" ({item['percent_change']:.4g}%)." if item['percent_change'] is not None else "."))
                            if item.get("assumption"):
                                st.caption("Assumption: " + item["assumption"])
                    else:
                        st.write("The checks did not change " + component["key"] + ".")
                for message in review["unknown_impacts"]:
                    st.write(message)
            recovery = result.get("recovery")
            if recovery:
                for requirement in recovery["requirements"]:
                    st.write(requirement)
                if recovery.get("code_template"):
                    with st.expander("Ready-to-run template"):
                        st.code(recovery["code_template"], language="python")
                if recovery.get("resume_url"):
                    with st.form("rate"):
                        rate = st.number_input("Exchange rate", min_value=0.000000000001, max_value=1000000.0, value=1.0, format="%.8f")
                        source = st.text_input("Rate source")
                        if st.form_submit_button("Supply rate & verify answer"):
                            analysis_created(api(f"/analyses/{analysis_id}/recovery", "POST", json={"rate": rate, "rate_source": source}))
            st.download_button("Download result", json.dumps(result, indent=2), "surecount-result.json", mime="application/json")
            if st.button("Run again"):
                analysis_created(api(f"/analyses/{analysis_id}/reruns", "POST"))
        else:
            st.info("Working on your answer. Click Refresh status in a moment.")
            if st.button("Cancel analysis"):
                api(f"/analyses/{analysis_id}/cancel", "POST")
                st.rerun()
except (requests.RequestException, RuntimeError) as exc:
    st.error(str(exc))
