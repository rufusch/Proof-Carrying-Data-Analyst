from .common import now, uid, Problem, TERMINAL
from .contract import validate
from .results import empty_result
from .store import jobs


class Service:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings

    def analysis(self, conn, request, parent=None, answers=None):
        dataset = self.store.get(conn, request["dataset_id"], "dataset")
        if dataset["public"]["status"] != "ready":
            raise Problem(409, "DATASET_NOT_READY", "The dataset is not ready for analysis.", True, {"current_status": dataset["public"]["status"]})
        id = uid()
        public = {"id": id, "dataset_id": request["dataset_id"], "parent_analysis_id": parent, "status": "queued", "outcome": None,
            "stage": {"code": "queued", "label": "Queued", "progress_percent": 0}, "created_at": now(), "updated_at": now(), "clarification": None, "failure": None}
        validate("Analysis", public)
        body = {"public": public, "request": request, "answers": answers or [], "attempt_count": 0}
        self.store.put(conn, id, "analysis", body, dataset["public"]["expires_at"])
        self.store.enqueue(conn, "analysis", id, self.settings.max_jobs)
        self.store.event(conn, id, "snapshot", public)
        return public

    def transition(self, conn, record, state, percent, outcome=None, clarification=None, failure=None):
        public = record["public"]
        if public["status"] in TERMINAL:
            raise Problem(409, "ALREADY_TERMINAL", "This analysis is already terminal.")
        public.update(status=state, outcome=outcome, clarification=clarification, failure=failure, updated_at=now(),
            stage={"code": state, "label": state.replace("_", " ").capitalize(), "progress_percent": percent})
        validate("Analysis", public)
        self.store.put(conn, public["id"], "analysis", record)
        self.store.event(conn, public["id"], "stage.changed", {"analysis_id": public["id"], "stage": public["stage"], "occurred_at": now()})
        if state == "needs_clarification":
            self.store.event(conn, public["id"], "clarification.required", {"analysis_id": public["id"], "clarification": clarification, "occurred_at": now()})
        if state in TERMINAL:
            conn.execute(jobs.update().where((jobs.c.resource_id == public["id"]) & (jobs.c.state == "queued")).values(state="done"))
            self.store.event(conn, public["id"], "analysis.terminal", {"analysis_id": public["id"], "status": state, "outcome": outcome, "occurred_at": now()})
        return public

    def terminal(self, conn, record, state, reason, retryable=False):
        result=empty_result(record["public"]["id"],state,reason)
        if state=='refused':
            from .recovery import recovery_for
            dataset=self.store.get(conn,record['public']['dataset_id'],'dataset')
            result['recovery']=recovery_for(record['request']['question'],dataset['profile'],record['answers'],reason)
            if result['recovery']['parameters']:result['recovery']['resume_url']=f'/api/v1/analyses/{record["public"]["id"]}/recovery'
            result['review']={'two_analyst':{'status':'not_run','analyst_a':'Agent 1','analyst_b':'Agent 2'},'skeptic':{'status':'not_run','checks':[]},'sensitivity':[], 'unknown_impacts':['No candidate result is published. Missing input impacts cannot be bounded without supplied values or bounds.'],'heatmap':[]}
        record["result"] = validate("AnalysisResult",result)
        return self.transition(conn, record, state, 100, outcome=state,
            failure={"code": state.upper(), "message": reason, "retryable": retryable} if state == "failed" else None)
