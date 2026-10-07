"""Run separately: python -m backend.worker. Jobs survive API/worker restarts."""
import json
import shutil
import threading
import time
from pathlib import Path
from uuid import UUID
from sqlalchemy import select, delete

from .common import Problem, TERMINAL, canonical, digest, now, uid
from .config import Settings
from .contract import validate
from .engine import prepare, UnsafePlan
from .planner import Planner, Decision
from .results import package
from .sandbox import DockerSandbox, SandboxUnavailable, SandboxRejected
from .service import Service
from .store import Store, resources, jobs, keys, events, heartbeats


class Aborted(Exception):
    pass


class Worker:
    def __init__(self, settings, store, sandbox=None, planner=None):
        self.settings, self.store = settings, store
        self.sandbox = sandbox or DockerSandbox(settings)
        self.planner = planner or Planner(settings)
        self.service = Service(store, settings)
        self.id = uid()
        self.active_job = None
        self.checks = {"sandbox": "not_ready", "model": "not_ready"}

    def heartbeat(self):
        with self.store.tx() as conn:
            conn.execute(delete(heartbeats).where(heartbeats.c.id == self.id))
            conn.execute(heartbeats.insert().values(id=self.id, time=time.time(), checks=canonical(self.checks)))
            if self.active_job:
                conn.execute(jobs.update().where((jobs.c.id == self.active_job["id"]) & (jobs.c.token == self.active_job["token"])).values(lease=time.time() + 600))

    def live_record(self, conn, job):
        lease = conn.execute(select(jobs).where(jobs.c.id == job["id"])).mappings().first()
        if not lease or lease["token"] != job["token"] or lease["state"] != "running":
            raise Aborted()
        record = self.store.get(conn, job["resource_id"])
        if record["public"]["status"] in TERMINAL or record["public"]["status"] in {"ready", "needs_clarification"}:
            raise Aborted()
        return record

    def stage(self, job, state, progress):
        with self.store.tx() as conn:
            record = self.live_record(conn, job)
            self.service.transition(conn, record, state, progress)
            return record

    def ingest(self, job):
        with self.store.tx() as conn:
            record = self.live_record(conn, job)
        dataset = record["public"]
        root = self.settings.data_dir / dataset["id"]
        output = self.sandbox.run(root, {"mode": "ingest", "dataset": dataset,
            "limits": {"max_rows": self.settings.max_rows, "max_columns": self.settings.max_columns, "max_tables": self.settings.max_tables}})
        profile = validate("DatasetProfile", output["profile"])
        content = canonical(output["tables"]).encode()
        # A stale/cancelled job cannot publish a new artifact.
        with self.store.tx() as conn:
            record = self.live_record(conn, job)
            artifact = root / "tables.json"
            artifact.write_bytes(content)
            artifact.chmod(0o644)
            record["tables_hash"] = digest(content)
            record["profile"] = profile
            record["vocabulary"] = output.get("vocabulary")
            record["public"].update(status="ready", table_count=len(profile["tables"]), warnings=profile["warnings"])
            validate("Dataset", record["public"])
            self.store.put(conn, dataset["id"], "dataset", record)

    def analyze(self, job):
        with self.store.tx() as conn:
            record = self.live_record(conn, job)
        record = self.stage(job, "repairing", 35) if "decision" in record else self.stage(job, "analyzing_question", 5)
        with self.store.tx() as conn:
            dataset = self.store.get(conn, record["public"]["dataset_id"], "dataset")
        request = record["request"]
        profile = dataset["profile"]
        if self.settings.planner == 'deterministic' and not dataset.get('vocabulary'):
            from .dataset_vocabulary import build_vocabulary
            artifact = (self.settings.data_dir / dataset['public']['id'] / 'tables.json').read_bytes()
            if digest(artifact) != dataset['tables_hash']:
                raise UnsafePlan('The stored dataset changed; upload a fresh copy before asking another question.')
            dataset['vocabulary'] = build_vocabulary(profile, json.loads(artifact))
            with self.store.tx() as conn:
                current = self.store.get(conn, dataset['public']['id'], 'dataset')
                if current and current['public']['status'] == 'ready' and current['tables_hash'] == dataset['tables_hash']:
                    current['vocabulary'] = dataset['vocabulary']
                    self.store.put(conn, dataset['public']['id'], 'dataset', current)
        repair = None
        while record["attempt_count"] < 3:
            planner_profile = {**profile, "_vocabulary": dataset.get("vocabulary")} if self.settings.planner == "deterministic" else profile
            decision = Decision.model_validate(record["decision"]) if "decision" in record else self.planner.decide(request["question"], planner_profile, record["answers"], request.get("preferences", {}), repair)
            with self.store.tx() as conn:
                record = self.live_record(conn, job)
                if decision.action == "clarify":
                    if len(record["answers"]) >= 5:
                        self.service.terminal(conn, record, "refused", "Ambiguity remains after five clarification rounds.")
                        return
                    if not decision.clarification_question or len({c.id for c in decision.choices}) != len(decision.choices):
                        self.service.terminal(conn, record, "refused", "The planner could not produce a valid clarification.")
                        return
                    clarification = {"id": uid(), "question": decision.clarification_question, "reason": decision.reason,
                        "choices": [c.model_dump() for c in decision.choices], "allow_free_text": True}
                    self.service.transition(conn, record, "needs_clarification", 10, clarification=clarification)
                    return
                if decision.action == "refuse" or decision.query is None:
                    self.service.terminal(conn, record, "refused", decision.reason)
                    return
                if decision.assumptions and not request.get("preferences", {}).get("allow_explicit_assumptions", True):
                    self.service.terminal(conn, record, "refused", "This plan requires assumptions that your preferences do not permit.")
                    return
                # Joining is material: require a recorded explicit confirmation, not an inferred key match.
                if decision.query.join and not any(a.get("join_confirmed") for a in record["answers"]):
                    if len(record["answers"]) >= 5:
                        self.service.terminal(conn, record, "refused", "The join relationship remains unconfirmed after five clarification rounds.")
                        return
                    join = decision.query.join
                    clarification = {"id": uid(), "question": f"Confirm inner join from {decision.query.table}.{join.left_column} to {join.table}.{join.right_column}?",
                        "reason": "Matching keys alone do not establish the meaning of a relationship. Execution also checks key uniqueness and coverage.",
                        "choices": [{"id": "confirm_join", "label": "Confirm this relationship", "description": "Proceed only if all left keys match one right row."}], "allow_free_text": True}
                    record["pending_join"] = {"base_table": decision.query.table, **join.model_dump()}
                    self.service.transition(conn, record, "needs_clarification", 10, clarification=clarification)
                    return
                if decision.query.join:
                    confirmed = next(a for a in record["answers"] if a.get("join_confirmed"))
                    if confirmed["join_confirmed"] != {"base_table": decision.query.table, **decision.query.join.model_dump()}:
                        self.service.terminal(conn, record, "refused", "The planned join differs from the confirmed relationship.")
                        return
                record["attempt_count"] += 1
                # Freeze interpretation once execution begins. A retry cannot drop outputs,
                # change filters, or choose another metric just to obtain passing checks.
                record["decision"] = decision.model_dump()
                self.store.put(conn, record["public"]["id"], "analysis", record)
            if record["attempt_count"] == 1:
                self.stage(job, "planning", 20)
            self.stage(job, "generating_code", 30)
            self.stage(job, "safety_check", 40)
            try:
                q, sql, _ = prepare(decision.query.model_dump(), profile, None)
            except UnsafePlan as exc:
                # This error text is authored by the allowlist validator, not an external library.
                with self.store.tx() as conn:
                    self.service.terminal(conn, self.live_record(conn, job), "refused", str(exc))
                return
            payload = {"mode": "execute", "dataset": dataset["public"], "profile": profile,
                "tables_hash": dataset["tables_hash"], "query": q.model_dump()}
            root = self.settings.data_dir / dataset["public"]["id"]
            self.stage(job, "executing", 55)
            output = self.sandbox.run(root, payload)
            self.stage(job, "verifying", 75)
            rerun = self.sandbox.run(root, payload)
            checks = [
                self.check("mechanical", "Approved SQL and canonical hash", output["sql"] == sql and digest(output["rows"]) == output["output_hash"], "Approved compiled query and valid result hash", "Matched" if output["sql"] == sql and digest(output["rows"]) == output["output_hash"] else "Mismatch"),
                self.check("independent_calculation", "Independent Decimal recalculation", output["independent_passed"] and rerun["independent_passed"], "Every group matches within rel=1e-10, abs=1e-9", "Matched" if output["independent_passed"] and rerun["independent_passed"] else "Mismatch"),
                self.check("clean_rerun", "Fresh isolated rerun", output["output_hash"] == rerun["output_hash"] and digest(rerun["rows"]) == rerun["output_hash"], output["output_hash"], rerun["output_hash"])]
            from .review import semantics_hash
            expected_semantics=semantics_hash(q,profile)
            checks += [self.check('units_filters','Identical units, filters and rate parameters',output.get('semantics_hash')==rerun.get('semantics_hash')==expected_semantics,expected_semantics,output.get('semantics_hash')),
                self.check('skeptic','Adversarial reviewer',output.get('review',{}).get('status')=='passed' and rerun.get('review',{}).get('status')=='passed','passed',output.get('review',{}).get('status','not_run'))]
            from .audit_code import generate
            replay_bytes = (root / 'tables.json').read_bytes()
            if digest(replay_bytes) != dataset['tables_hash']:
                raise UnsafePlan('The stored data changed before code packaging.')
            replay_tables = json.loads(replay_bytes)
            output["audit_code"] = generate(sql, profile, replay_tables, {q.table} | ({q.join.table} if q.join else set()))
            result, evidence = package(record["public"]["id"], dataset["public"], profile, q, decision, record["answers"], output, rerun, record["attempt_count"], checks, self.settings.planner == "openai" or decision.reason.startswith("Grounded"))
            with self.store.tx() as conn:
                latest = self.live_record(conn, job)
                latest.setdefault("attempt_history", []).append({"attempt": record["attempt_count"], "checks": checks})
                self.store.put(conn, latest["public"]["id"], "analysis", latest)
            if result["outcome"] == "answered" or output.get('review',{}).get('status')=='failed' or record["attempt_count"] >= 3:
                self.stage(job, "packaging_evidence", 90)
                with self.store.tx() as conn:
                    latest = self.live_record(conn, job)
                    # Preserve failed prior attempts in immutable evidence; only the final checks gate current claims.
                    evidence["checks"] = [{**c, "id": f'attempt_{h["attempt"]}_{c["id"]}'} for h in latest["attempt_history"] for c in h["checks"]]
                    latest["result"] = validate("AnalysisResult", result)
                    latest["evidence"] = validate("EvidencePackage", evidence)
                    self.service.transition(conn, latest, "completed" if result["outcome"] == "answered" else "refused", 100, outcome=result["outcome"])
                return
            repair = "Independent validation or clean rerun failed. Reconsider the same supported request; do not weaken verification or omit requested outputs."
            record = self.stage(job, "repairing", 35)
        with self.store.tx() as conn:
            self.service.terminal(conn, self.live_record(conn, job), "refused", "The three-attempt execution budget is exhausted.")

    @staticmethod
    def check(kind, name, passed, expected, actual):
        return {"id": kind, "type": kind, "name": name, "status": "passed" if passed else "failed", "expected": expected, "actual": actual,
            "explanation": "Deterministic check; independent of answer prose."}

    def run_once(self):
        job = self.store.claim()
        if job is None:
            return False
        self.active_job = job
        try:
            self.ingest(job) if job["kind"] == "dataset" else self.analyze(job)
        except (Aborted, Problem):
            pass  # Cancelled, expired, or superseded; do not resurrect it.
        except Exception as exc:
            rejected = isinstance(exc, SandboxRejected)
            reason = str(exc) if rejected else exc.public_message + " Check backend readiness and Docker, then retry the upload or analysis." if isinstance(exc, SandboxUnavailable) else "Processing failed before a verified output could be produced. Check the file format and backend readiness, then retry."
            with self.store.tx() as conn:
                try:
                    record = self.live_record(conn, job)
                    if job["kind"] == "dataset":
                        record["public"].update(status="failed", failure={"code": "INGESTION_FAILED", "message": reason, "retryable": not rejected})
                        self.store.put(conn, job["resource_id"], "dataset", record)
                    else:
                        self.service.terminal(conn, record, "refused" if rejected else "failed", reason, not rejected)
                except (Problem, Aborted):
                    pass
        finally:
            self.store.finish(job)
            self.active_job = None
        return True

    def cleanup(self):
        with self.store.tx() as conn:
            stale = conn.execute(select(resources).where(resources.c.expires <= now())).mappings().all()
            for row in stale:
                if row["kind"] == "dataset":
                    target = (self.settings.data_dir / row["id"]).resolve()
                    if target.parent == self.settings.data_dir.resolve() and target.is_dir():
                        shutil.rmtree(target)
                conn.execute(delete(events).where(events.c.resource_id == row["id"]))
                conn.execute(delete(jobs).where(jobs.c.resource_id == row["id"]))
            conn.execute(delete(resources).where(resources.c.expires <= now()))
            conn.execute(delete(keys).where(keys.c.expires <= now()))
            conn.execute(delete(heartbeats).where(heartbeats.c.time < time.time() - 3600))
            live_ids = set(conn.execute(select(resources.c.id).where(resources.c.kind == "dataset")).scalars())
            cutoff = time.time() - self.settings.retention_hours * 3600
            # Recover unregistered upload directories left behind by an interrupted process.
            for child in self.settings.data_dir.iterdir():
                if not child.is_dir() or child.name in live_ids:
                    continue
                owned = child.name.startswith("upload-")
                try:
                    owned = owned or str(UUID(child.name)) == child.name
                except ValueError:
                    pass
                if owned and child.resolve().parent == self.settings.data_dir.resolve() and child.stat().st_mtime < cutoff:
                    shutil.rmtree(child)

    def run(self):
        stop = threading.Event()
        def heartbeat_loop():
            while not stop.is_set():
                try:
                    self.heartbeat()
                except Exception:
                    pass
                stop.wait(5)
        thread = threading.Thread(target=heartbeat_loop, daemon=True)
        thread.start()
        refreshed = 0
        try:
            while True:
                if time.monotonic() - refreshed > 60:
                    self.checks = {"sandbox": "ready" if self.sandbox.ready() else "not_ready", "model": "ready" if self.planner.ready() else "not_ready"}
                    self.cleanup()
                    refreshed = time.monotonic()
                if not self.run_once():
                    time.sleep(.5)
        finally:
            stop.set()
            thread.join(timeout=6)


if __name__ == "__main__":
    settings = Settings()
    Worker(settings, Store(settings.database_url)).run()
