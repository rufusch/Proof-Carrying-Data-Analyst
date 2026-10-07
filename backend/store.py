"""Transactional durable queue, resources, replay events and idempotency records.

SQLite is for one-host development. PostgreSQL uses a transaction-scoped advisory
lock for short control-plane mutations; computation always happens outside it.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from sqlalchemy import create_engine, MetaData, Table, Column, String, Integer, Text, Float, select, text, delete, inspect, func
from sqlalchemy.pool import StaticPool
import json

from .common import now, expires, canonical, Problem, uid

metadata = MetaData()
resources = Table("resources", metadata,
    Column("id", String, primary_key=True), Column("kind", String, nullable=False),
    Column("expires", String, nullable=False), Column("body", Text, nullable=False))
keys = Table("idempotency", metadata, Column("id", String, primary_key=True),
    Column("hash", String, nullable=False), Column("expires", String, nullable=False), Column("body", Text, nullable=False))
jobs = Table("jobs", metadata, Column("id", String, primary_key=True), Column("kind", String),
    Column("resource_id", String, index=True), Column("state", String, index=True), Column("lease", Float), Column("token", String),
    Column("created_at", Float, nullable=False, server_default=text("0")))
events = Table("events", metadata, Column("seq", Integer, primary_key=True, autoincrement=True),
    Column("resource_id", String, index=True), Column("name", String), Column("body", Text))
heartbeats = Table("heartbeats", metadata, Column("id", String, primary_key=True), Column("time", Float), Column("checks", Text))
schema_versions = Table("schema_versions", metadata, Column("version", Integer, primary_key=True), Column("applied_at", String, nullable=False))


class Store:
    def __init__(self, url):
        kwargs = {"connect_args": {"check_same_thread": False, "timeout": 30}} if url.startswith("sqlite") else {}
        if url == "sqlite://":
            kwargs["poolclass"] = StaticPool
        self.engine = create_engine(url, pool_pre_ping=True, **kwargs)
        self.migrate()

    def migrate(self):
        """Initial versioned additive migration; serialized across API/worker startup."""
        with self.tx() as conn:
            metadata.create_all(conn)
            version = conn.execute(select(func.max(schema_versions.c.version))).scalar() or 0
            if version > 1:
                raise RuntimeError("Database schema is newer than this backend; use a compatible version.")
            if version < 1:
                columns = {c["name"] for c in inspect(conn).get_columns("jobs")}
                if "created_at" not in columns:
                    conn.execute(text("ALTER TABLE jobs ADD COLUMN created_at DOUBLE PRECISION NOT NULL DEFAULT 0"))
                conn.execute(schema_versions.insert().values(version=1, applied_at=now()))

    @contextmanager
    def tx(self):
        with self.engine.connect() as conn:
            try:
                if self.engine.dialect.name == "sqlite":
                    conn.exec_driver_sql("BEGIN IMMEDIATE")
                else:
                    conn.execute(text("SELECT pg_advisory_xact_lock(742031)"))
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def get(self, conn, id, kind=None):
        row = conn.execute(select(resources).where(resources.c.id == id)).mappings().first()
        if not row or row["expires"] <= now() or (kind and row["kind"] != kind):
            raise Problem(404, "NOT_FOUND", "Resource not found or expired.")
        return json.loads(row["body"])

    def put(self, conn, id, kind, body, expiry=None):
        record = {"id": id, "kind": kind, "body": canonical(body)}
        existing = conn.execute(select(resources.c.id).where(resources.c.id == id)).first()
        if existing:
            conn.execute(resources.update().where(resources.c.id == id).values(body=record["body"]))
        else:
            conn.execute(resources.insert().values(**record, expires=expiry or expires(24)))

    def replay(self, conn, scope, key, fingerprint):
        if key is None:
            return None
        if not 8 <= len(key) <= 128:
            raise Problem(422, "INVALID_IDEMPOTENCY_KEY", "Idempotency-Key must contain 8–128 characters.")
        id = scope + ":" + key
        row = conn.execute(select(keys).where(keys.c.id == id)).mappings().first()
        if row and row["expires"] > now():
            if row["hash"] != fingerprint:
                raise Problem(409, "IDEMPOTENCY_CONFLICT", "This key was used with a different request.")
            return json.loads(row["body"])
        if row:
            conn.execute(delete(keys).where(keys.c.id == id))
        return None

    def remember(self, conn, scope, key, fingerprint, body):
        if key is not None:
            conn.execute(keys.insert().values(id=scope + ":" + key, hash=fingerprint, expires=expires(24), body=canonical(body)))

    def enqueue(self, conn, kind, id, max_jobs=100):
        active = conn.execute(select(func.count()).select_from(jobs).where(jobs.c.state.in_(["queued", "running"]))).scalar()
        if active >= max_jobs:
            raise Problem(429, "CAPACITY_EXCEEDED", "The worker queue is full.", True)
        conn.execute(jobs.insert().values(id=uid(), kind=kind, resource_id=id, state="queued", lease=0, token="", created_at=datetime.now(timezone.utc).timestamp()))

    def event(self, conn, id, name, body):
        conn.execute(events.insert().values(resource_id=id, name=name, body=canonical(body)))

    def claim(self):
        timestamp = datetime.now(timezone.utc).timestamp()
        with self.tx() as conn:
            other = jobs.alias("other_job")
            running_same_resource = select(other.c.id).where((other.c.resource_id == jobs.c.resource_id) & (other.c.id != jobs.c.id) & (other.c.state == "running") & (other.c.lease >= timestamp)).exists()
            row = conn.execute(select(jobs).where(((jobs.c.state == "queued") | ((jobs.c.state == "running") & (jobs.c.lease < timestamp))) & ~running_same_resource).order_by(jobs.c.created_at, jobs.c.id).limit(1)).mappings().first()
            if row:
                token = uid()
                conn.execute(jobs.update().where(jobs.c.id == row["id"]).values(state="running", lease=timestamp + 600, token=token))
                return {**dict(row), "token": token}

    def finish(self, job):
        with self.tx() as conn:
            conn.execute(jobs.update().where((jobs.c.id == job["id"]) & (jobs.c.token == job["token"])).values(state="done"))
