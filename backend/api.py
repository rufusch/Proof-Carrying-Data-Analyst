import asyncio
import hashlib
import json
import shutil
import tempfile
import time
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from jsonschema import ValidationError
from sqlalchemy import select, text
from starlette.exceptions import HTTPException
from starlette.datastructures import UploadFile

from .common import Problem, TERMINAL, canonical, digest, expires, now, uid
from .config import Settings
from .contract import DOCUMENT, validate
from .service import Service
from .store import Store, events, heartbeats


def create_app(settings=None, store=None):
    settings = settings or Settings()
    store = store or Store(settings.database_url)
    service = Service(store, settings)
    app = FastAPI(title="Proof-Carrying Data Analyst", openapi_url="/api/v1/openapi.json", docs_url="/api/v1/docs")
    app.openapi = lambda: DOCUMENT
    app.state.store, app.state.settings = store, settings
    @app.get("/", include_in_schema=False)
    async def test_frontend():
        return FileResponse(Path(__file__).resolve().parents[1] / "frontend" / "index.html", headers={"Cache-Control": "no-store"})

    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Idempotency-Key", "Last-Event-ID"], expose_headers=["X-Request-ID", "Retry-After"])

    def error(request, status, code, message, retryable=False, details=None):
        body = {"error": {"code": code, "message": message, "request_id": getattr(request.state, "request_id", uid()), "retryable": retryable, "details": details or {}}}
        validate("ErrorEnvelope", body)
        return JSONResponse(body, status_code=status, headers={"X-Request-ID": body["error"]["request_id"], **({"Retry-After": "5"} if status == 429 else {})})

    @app.middleware("http")
    async def request_boundary(request, call_next):
        request.state.request_id = uid()
        maximum = settings.max_dataset_bytes + 1024 * 1024 if request.url.path == "/api/v1/datasets" else 64 * 1024
        length = request.headers.get("content-length")
        if length is not None:
            try:
                if int(length) > maximum or int(length) < 0:
                    raise Problem(413, "REQUEST_TOO_LARGE", "Request body exceeds the effective limit.")
            except ValueError:
                return error(request, 400, "MALFORMED_INPUT", "Invalid Content-Length.")
            except Problem as exc:
                return error(request, exc.status, exc.code, exc.message)
        receive = request._receive
        size = 0
        body_limit_exceeded = False
        async def limited_receive():
            nonlocal size, body_limit_exceeded
            message = await receive()
            size += len(message.get("body", b""))
            if size > maximum:
                body_limit_exceeded = True
                raise Problem(413, "REQUEST_TOO_LARGE", "Request body exceeds the effective limit.")
            return message
        request._receive = limited_receive
        try:
            response = await call_next(request)
        except Exception:
            response = error(request, 500, "INTERNAL_ERROR", "An unexpected backend failure occurred.", True)
        # ASGI's receive bridge may wrap the limiter exception in an exception group.
        # Preserve its authoritative HTTP status even when a parser catches/wraps it.
        if body_limit_exceeded:
            response = error(request, 413, "REQUEST_TOO_LARGE", "Request body exceeds the effective limit.")
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(Problem)
    async def domain_error(request, exc):
        return error(request, exc.status, exc.code, exc.message, exc.retryable, exc.details)

    @app.exception_handler(RequestValidationError)
    async def request_error(request, exc):
        return error(request, 422, "VALIDATION_ERROR", "Request fields are invalid.")

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error(request, exc.status_code, "HTTP_ERROR", "Request could not be processed.")

    def respond(schema, body, status=200):
        return JSONResponse(validate(schema, body), status_code=status)

    async def json_body(request, schema=None, optional=False):
        data = await request.body()
        if not data and optional:
            return {}
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise Problem(415, "UNSUPPORTED_MEDIA_TYPE", "Use application/json.")
        try:
            def reject_constant(value):
                raise ValueError("non-finite")
            value = json.loads(data, parse_constant=reject_constant)
        except (ValueError, UnicodeDecodeError):
            raise Problem(400, "MALFORMED_JSON", "The request is not valid JSON.")
        if schema:
            try:
                validate(schema, value)
            except ValidationError:
                raise Problem(422, "VALIDATION_ERROR", "Request fields are invalid.")
        return value

    def mutate(request, payload, action):
        scope = request.url.path
        key = request.headers.get("idempotency-key")
        fingerprint = digest(payload)
        with store.tx() as conn:
            previous = store.replay(conn, scope, key, fingerprint)
            if previous is not None:
                return previous
            body = action(conn)
            store.remember(conn, scope, key, fingerprint, body)
            return body

    @app.get("/api/v1/health")
    def health():
        return respond("Health", {"status": "ok", "version": "0.1.0"})

    @app.get("/api/v1/ready")
    def ready(request: Request):
        checks = {k: "not_ready" for k in ["database", "storage", "queue", "worker", "sandbox", "model"]}
        try:
            with store.tx() as conn:
                conn.execute(text("SELECT 1"))
                checks["database"] = checks["queue"] = "ready"
                beats = conn.execute(select(heartbeats).where(heartbeats.c.time > time.time() - 20)).mappings().all()
                if beats:
                    checks["worker"] = "ready"
                    for key in ("sandbox", "model"):
                        checks[key] = "ready" if any(json.loads(b["checks"]).get(key) == "ready" for b in beats) else "not_ready"
            with tempfile.TemporaryFile(dir=settings.data_dir) as probe:
                probe.write(b"ready")
                probe.flush()
            checks["storage"] = "ready"
        except Exception:
            pass
        if any(v != "ready" for v in checks.values()):
            return error(request, 503, "DEPENDENCY_UNAVAILABLE", "One or more backend dependencies are not ready.", True, {"checks": checks})
        return respond("Readiness", {"status": "ready", "checks": checks})

    @app.get("/api/v1/capabilities")
    def capabilities():
        return respond("Capabilities", {"contract_version": "1.0", "file_extensions": [".csv", ".xlsx", ".xls"],
            "limits": {"max_files": settings.max_files, "max_file_bytes": settings.max_file_bytes, "max_dataset_bytes": settings.max_dataset_bytes,
                "retention_hours": settings.retention_hours, "max_rows": settings.max_rows, "max_columns": settings.max_columns, "max_tables": settings.max_tables},
            "result_types": ["text", "metric", "table", "chart"],
            "features": {"sse": True, "clarifications": True, "reruns": True, "sql": True, "arbitrary_python": False, "forecasting": False, "causal_analysis": False, "growth": True, "ratios": True, "multiple_aggregates": True, "model_planner": settings.planner == "openai", "semantic_questions": True, "multiple_files": True},
            "planner": settings.planner, "supported_operations": ["count", "sum", "mean", "min", "max", "ratio", "percentage", "growth", "group_by", "filters", "many_to_one_inner_join"]})

    @app.post("/api/v1/datasets")
    async def upload(request: Request):
        if not request.headers.get("content-type", "").startswith("multipart/form-data"):
            raise Problem(415, "UNSUPPORTED_MEDIA_TYPE", "Use multipart/form-data with repeated files fields.")
        staging = Path(tempfile.mkdtemp(prefix="upload-", dir=settings.data_dir))
        destination = None
        committed = False
        try:
            async with request.form(max_files=settings.max_files, max_fields=1, max_part_size=4096) as form:
                files = form.getlist("files")
                name = form.get("name")
                if set(form.keys()) - {"files", "name"} or not 1 <= len(files) <= settings.max_files or any(not isinstance(f, UploadFile) for f in files):
                    raise Problem(422, "VALIDATION_ERROR", "Supply 1–10 files in the files field.")
                if name is not None and (not isinstance(name, str) or not 1 <= len(name.strip()) <= 100):
                    raise Problem(422, "VALIDATION_ERROR", "Dataset name must contain 1–100 characters.")
                metadata, total = [], 0
                for upload in files:
                    filename = (upload.filename or "").replace("\\", "/").split("/")[-1]
                    extension = Path(filename).suffix.lower()
                    media = {".csv": "text/csv", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xls": "application/vnd.ms-excel"}
                    if extension not in media:
                        raise Problem(415, "UNSUPPORTED_FILE_TYPE", "Only CSV, XLSX and XLS files are accepted.")
                    if len(filename) > 255 or any(ord(c) < 32 for c in filename):
                        raise Problem(422, "INVALID_FILENAME", "Filename is invalid.")
                    file_id, size, sha = uid(), 0, hashlib.sha256()
                    with (staging / file_id).open("xb") as target:
                        while chunk := await upload.read(1024 * 1024):
                            size += len(chunk)
                            total += len(chunk)
                            if size > settings.max_file_bytes or total > settings.max_dataset_bytes:
                                raise Problem(413, "UPLOAD_TOO_LARGE", "File or dataset exceeds the effective upload limits.")
                            sha.update(chunk)
                            target.write(chunk)
                    if not size:
                        raise Problem(422, "EMPTY_FILE", "Uploaded files must not be empty.")
                    metadata.append({"id": file_id, "filename": filename, "media_type": media[extension], "size_bytes": size, "sha256": sha.hexdigest()})
                fingerprint = {"name": name, "files": [{k: v for k, v in f.items() if k != "id"} for f in metadata]}
                def create(conn):
                    nonlocal destination
                    id = uid()
                    public = validate("Dataset", {"id": id, "name": name or "Untitled dataset", "status": "profiling", "created_at": now(), "expires_at": expires(settings.retention_hours), "files": metadata, "table_count": None, "warnings": []})
                    store.enqueue(conn, "dataset", id, settings.max_jobs)
                    destination = settings.data_dir / id
                    staging.chmod(0o755)
                    for source in staging.iterdir():
                        source.chmod(0o644)
                    staging.rename(destination)
                    store.put(conn, id, "dataset", {"public": public}, public["expires_at"])
                    return public
                body = mutate(request, fingerprint, create)
                committed = True
                return respond("Dataset", body, 202)
        finally:
            if staging.is_dir():
                shutil.rmtree(staging)
            if not committed and destination is not None and destination.is_dir():
                shutil.rmtree(destination)

    @app.get("/api/v1/datasets/{dataset_id}")
    def get_dataset(dataset_id: UUID):
        with store.tx() as conn:
            return respond("Dataset", store.get(conn, str(dataset_id), "dataset")["public"])

    @app.get("/api/v1/datasets/{dataset_id}/profile")
    def get_profile(dataset_id: UUID):
        with store.tx() as conn:
            record = store.get(conn, str(dataset_id), "dataset")
            if record["public"]["status"] != "ready":
                raise Problem(409, "DATASET_NOT_READY", "The profile is not ready.", True)
            return respond("DatasetProfile", record["profile"])

    @app.post("/api/v1/analyses")
    async def create_analysis(request: Request):
        body = await json_body(request, "CreateAnalysisRequest")
        if not body["question"].strip():
            raise Problem(422, "VALIDATION_ERROR", "Question cannot be blank.")
        public = mutate(request, body, lambda conn: service.analysis(conn, body))
        return respond("Analysis", public, 202)

    @app.get("/api/v1/analyses/{analysis_id}")
    def get_analysis(analysis_id: UUID):
        with store.tx() as conn:
            return respond("Analysis", store.get(conn, str(analysis_id), "analysis")["public"])

    @app.post("/api/v1/analyses/{analysis_id}/clarifications")
    async def clarification(analysis_id: UUID, request: Request):
        answer = await json_body(request, "ClarificationAnswer")
        def apply(conn):
            record = store.get(conn, str(analysis_id), "analysis")
            current = record["public"]["clarification"]
            if record["public"]["status"] != "needs_clarification" or not current or current["id"] != answer["clarification_id"]:
                raise Problem(409, "STALE_CLARIFICATION", "This clarification is no longer active.")
            if "choice_id" in answer and answer["choice_id"] not in {c["id"] for c in current["choices"]}:
                raise Problem(422, "INVALID_CHOICE", "Select a choice from the active clarification.")
            value = answer.get("choice_id", answer.get("text", "")).strip()
            if not value:
                raise Problem(422, "INVALID_ANSWER", "Clarification answer cannot be blank.")
            supplied = {"question": current["question"], "value": value}
            if value == "confirm_join" and "pending_join" in record:
                supplied["join_confirmed"] = record.pop("pending_join")
            record["answers"].append(supplied)
            store.enqueue(conn, "analysis", str(analysis_id), settings.max_jobs)
            return service.transition(conn, record, "queued", 0)
        return respond("Analysis", mutate(request, answer, apply), 202)

    @app.get("/api/v1/analyses/{analysis_id}/result")
    def result(analysis_id: UUID):
        with store.tx() as conn:
            record = store.get(conn, str(analysis_id), "analysis")
            if record["public"]["status"] not in TERMINAL:
                raise Problem(409, "RESULT_NOT_READY", "The analysis is not terminal.", True)
            return respond("AnalysisResult", record["result"])

    @app.post('/api/v1/analyses/{analysis_id}/recovery')
    async def recover(analysis_id: UUID, request: Request):
        body=await json_body(request,'RecoveryAnswer')
        def create(conn):
            original=store.get(conn,str(analysis_id),'analysis')
            recovery=original.get('result',{}).get('recovery')
            if original['public']['status']!='refused' or not recovery or not recovery.get('parameters'):
                raise Problem(409,'RECOVERY_UNAVAILABLE','This analysis has no exchange-rate recovery parameter.')
            label=f'Exchange rate {recovery["source_currency"]} to {recovery["target_currency"]}'
            answers=original['answers']+[{'question':label,'value':str(body['rate'])},{'question':'Exchange-rate provenance','value':body['rate_source']}]
            return service.analysis(conn,original['request'],str(analysis_id),answers)
        return respond('Analysis',mutate(request,body,create),202)

    @app.get("/api/v1/analyses/{analysis_id}/evidence")
    def evidence(analysis_id: UUID):
        with store.tx() as conn:
            record = store.get(conn, str(analysis_id), "analysis")
            if "evidence" not in record:
                raise Problem(409, "EVIDENCE_UNAVAILABLE", "No execution evidence is available for this analysis.")
            return respond("EvidencePackage", record["evidence"])

    @app.post("/api/v1/analyses/{analysis_id}/reruns")
    async def rerun(analysis_id: UUID, request: Request):
        body = await json_body(request, optional=True)
        if not isinstance(body, dict) or set(body) - {"question"} or ("question" in body and (not isinstance(body["question"], str) or not 3 <= len(body["question"].strip()) <= 4000)):
            raise Problem(422, "VALIDATION_ERROR", "Rerun question must contain 3–4000 characters.")
        def create(conn):
            original = store.get(conn, str(analysis_id), "analysis")
            if original["public"]["status"] not in TERMINAL:
                raise Problem(409, "ANALYSIS_NOT_TERMINAL", "Wait for the original analysis to finish.")
            payload = {**original["request"], **body}
            return service.analysis(conn, payload, str(analysis_id), original["answers"] if not body else [])
        return respond("Analysis", mutate(request, body, create), 202)

    @app.post("/api/v1/analyses/{analysis_id}/cancel")
    async def cancel(analysis_id: UUID, request: Request):
        raw = await request.body()
        if raw:
            raise Problem(422, "VALIDATION_ERROR", "Cancellation does not accept a request body.")
        def apply(conn):
            record = store.get(conn, str(analysis_id), "analysis")
            if record["public"]["status"] == "cancelled":
                return record["public"]
            return service.terminal(conn, record, "cancelled", "Analysis cancelled by the user.")
        return respond("Analysis", mutate(request, {}, apply), 202)

    @app.get("/api/v1/analyses/{analysis_id}/events")
    async def stream(analysis_id: UUID, request: Request):
        id = str(analysis_id)
        with store.tx() as conn:
            public = store.get(conn, id, "analysis")["public"]
            latest = conn.execute(select(events.c.seq).where(events.c.resource_id == id).order_by(events.c.seq.desc()).limit(1)).scalar() or 0
            header = request.headers.get("last-event-id", "")
            replay_from = None
            try:
                candidate = int(header)
                if conn.execute(select(events.c.seq).where((events.c.seq == candidate) & (events.c.resource_id == id))).first():
                    replay_from = candidate
            except ValueError:
                pass
        def encode(seq, event, data):
            return f"id: {seq}\nevent: {event}\ndata: {canonical(data)}\n\n"
        async def generate():
            # Snapshot first, then at-least-once replay. Snapshot IDs are distinct from durable event IDs.
            yield encode("snapshot-" + uid(), "snapshot", public)
            cursor = replay_from if replay_from is not None else latest
            last_heartbeat = time.monotonic()
            while not await request.is_disconnected():
                try:
                    with store.tx() as conn:
                        current = store.get(conn, id, "analysis")["public"]
                        batch = conn.execute(select(events).where((events.c.resource_id == id) & (events.c.seq > cursor)).order_by(events.c.seq).limit(100)).mappings().all()
                except Problem:
                    return
                for event in batch:
                    cursor = event["seq"]
                    yield encode(cursor, event["name"], json.loads(event["body"]))
                if current["status"] in TERMINAL and len(batch) < 100:
                    return
                if time.monotonic() - last_heartbeat >= 15:
                    yield encode("heartbeat-" + uid(), "heartbeat", {"occurred_at": now()})
                    last_heartbeat = time.monotonic()
                await asyncio.sleep(.25)
        return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app


app = create_app()
