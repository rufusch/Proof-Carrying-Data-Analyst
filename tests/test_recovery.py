import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select, text

from backend.store import Store, jobs, schema_versions
from backend.worker import Aborted
from conftest import dataset, analysis


def test_migration_upgrades_old_queue_without_dropping_jobs(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE jobs (id VARCHAR PRIMARY KEY, kind VARCHAR, resource_id VARCHAR, state VARCHAR, lease FLOAT, token VARCHAR)")
        connection.execute("INSERT INTO jobs VALUES ('old', 'analysis', 'resource', 'queued', 0, '')")
    store = Store("sqlite:///" + path.as_posix())
    with store.tx() as conn:
        old = conn.execute(select(jobs)).mappings().one()
        assert old["id"] == "old" and old["created_at"] == 0
        assert conn.execute(select(schema_versions.c.version)).scalar() == 1
    store.migrate()
    assert store.claim()["id"] == "old"
    store.engine.dispose()


def test_concurrent_initialization_is_safe(tmp_path):
    url = "sqlite:///" + (tmp_path / "startup.db").as_posix()
    def initialize(_):
        store = Store(url)
        with store.tx() as conn:
            assert conn.execute(select(schema_versions.c.version)).scalar() == 1
        store.engine.dispose()
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(initialize, range(4)))


def test_queue_is_fifo_and_prevents_duplicate_resource_execution(system):
    _, _, store, *_ = system
    with store.tx() as conn:
        for name in ["first", "second", "first"]:
            store.enqueue(conn, "analysis", name)
    one = store.claim()
    two = store.claim()
    assert one["resource_id"] == "first" and two["resource_id"] == "second"
    assert store.claim() is None
    store.finish(one)
    assert store.claim()["resource_id"] == "first"


def test_stale_worker_cannot_publish_after_lease_replacement(system):
    client, worker, store, *_ = system
    client.post("/api/v1/datasets", files=[("files", ("a.csv", b"amount\n1\n"))])
    first = store.claim()
    with store.tx() as conn:
        conn.execute(jobs.update().where(jobs.c.id == first["id"]).values(lease=time.time() - 1))
    second = store.claim()
    assert second["id"] == first["id"] and second["token"] != first["token"]
    with store.tx() as conn, pytest.raises(Aborted):
        worker.live_record(conn, first)


def test_crash_after_clarification_does_not_regenerate_question(system):
    did = dataset(system, [("files", ("a.csv", b"amount\n1\n")), ("files", ("b.csv", b"amount\n2\n"))])
    aid = analysis(system, did)
    client, worker, store, *_ = system
    before = client.get(f"/api/v1/analyses/{aid}").json()
    with store.tx() as conn:
        conn.execute(jobs.update().where(jobs.c.resource_id == aid).values(state="running", lease=0))
    assert worker.run_once()
    assert client.get(f"/api/v1/analyses/{aid}").json() == before


def test_cancelled_queued_job_releases_capacity(system):
    did = dataset(system)
    client, _, store, settings, _ = system
    settings.max_jobs = 1
    one = client.post("/api/v1/analyses", json={"dataset_id": did, "question": "sum amount"}).json()
    assert client.post(f'/api/v1/analyses/{one["id"]}/cancel').status_code == 202
    assert client.post("/api/v1/analyses", json={"dataset_id": did, "question": "sum amount"}).status_code == 202
