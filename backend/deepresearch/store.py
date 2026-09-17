"""Single-process SQLite store. Each operation runs off the event loop.

Success caches are durable independently of LangGraph checkpoints. A remote call
that succeeds immediately before process death is still at-least-once, not exactly-once.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Callable
from pathlib import Path

from .contracts import ResearchError, utcnow


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


class Store:
    def __init__(self, path: Path):
        self.path = path

    def _call(self, fn: Callable):
        db = sqlite3.connect(self.path, timeout=30)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            result = fn(db)
            db.commit()
            return result
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    async def call(self, fn):
        # Cancelling to_thread does not stop its SQLite transaction. Drain it
        # before cancellation is acknowledged, rather than allowing late writes
        # after cancel/shutdown has returned to the caller.
        task = asyncio.create_task(asyncio.to_thread(self._call, fn))
        interrupted = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                interrupted = True
        if interrupted:
            # Retrieve exceptions so a cancelled caller cannot leak task errors.
            if not task.cancelled():
                task.exception()
            raise asyncio.CancelledError
        return task.result()

    async def start(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)

        def init(db):
            db.executescript("""
            CREATE TABLE IF NOT EXISTS research_run (
              id TEXT PRIMARY KEY, owner TEXT NOT NULL, request_key TEXT NOT NULL,
              request_hash TEXT NOT NULL, body TEXT NOT NULL,
              UNIQUE(owner, request_key));
            CREATE TABLE IF NOT EXISTS research_unit (
              run_id TEXT NOT NULL REFERENCES research_run(id), id TEXT NOT NULL,
              input_hash TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(run_id,id));
            CREATE TABLE IF NOT EXISTS research_evidence (
              run_id TEXT NOT NULL REFERENCES research_run(id), id TEXT NOT NULL,
              body TEXT NOT NULL, PRIMARY KEY(run_id,id));
            CREATE TABLE IF NOT EXISTS research_gap (
              run_id TEXT NOT NULL REFERENCES research_run(id), id TEXT NOT NULL,
              body TEXT NOT NULL, PRIMARY KEY(run_id,id));
            CREATE TABLE IF NOT EXISTS research_report (
              run_id TEXT NOT NULL REFERENCES research_run(id), version INTEGER NOT NULL,
              body TEXT NOT NULL, PRIMARY KEY(run_id,version));
            CREATE TABLE IF NOT EXISTS research_event (
              run_id TEXT NOT NULL REFERENCES research_run(id), seq INTEGER NOT NULL,
              key TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(run_id,seq), UNIQUE(run_id,key));
            CREATE TABLE IF NOT EXISTS research_cache (
              run_id TEXT NOT NULL REFERENCES research_run(id), key TEXT NOT NULL,
              body TEXT NOT NULL, PRIMARY KEY(run_id,key));
            CREATE TABLE IF NOT EXISTS research_source (
              run_id TEXT NOT NULL REFERENCES research_run(id), id TEXT NOT NULL,
              body TEXT NOT NULL, PRIMARY KEY(run_id,id));
            CREATE TABLE IF NOT EXISTS research_tool_call (
              run_id TEXT NOT NULL REFERENCES research_run(id), id TEXT NOT NULL,
              body TEXT NOT NULL, PRIMARY KEY(run_id,id));
            CREATE TABLE IF NOT EXISTS research_favicon (
              domain TEXT PRIMARY KEY, content_type TEXT, body BLOB, fetched_at REAL NOT NULL);
            PRAGMA user_version=1;
            """)

        await self.call(init)

    async def create(self, run, key, request_hash):
        def op(db):
            old = db.execute("SELECT body, request_hash FROM research_run WHERE owner=? AND request_key=?", (run["owner"], key)).fetchone()
            if old:
                if old["request_hash"] != request_hash:
                    raise ResearchError("IDEMPOTENCY_CONFLICT", "同一幂等键不能用于不同请求", recoverable=False)
                return json.loads(old["body"]), False
            db.execute("INSERT INTO research_run VALUES (?,?,?,?,?)", (run["run_id"], run["owner"], key, request_hash, dumps(run)))
            return run, True

        return await self.call(op)

    async def by_request(self, owner, key, request_hash):
        def op(db):
            row = db.execute("SELECT body, request_hash FROM research_run WHERE owner=? AND request_key=?", (owner, key)).fetchone()
            if row and row["request_hash"] != request_hash:
                raise ResearchError("IDEMPOTENCY_CONFLICT", "同一幂等键不能用于不同请求", recoverable=False)
            return json.loads(row["body"]) if row else None

        return await self.call(op)

    async def get(self, run_id):
        def op(db):
            row = db.execute("SELECT body FROM research_run WHERE id=?", (run_id,)).fetchone()
            return json.loads(row[0]) if row else None

        return await self.call(op)

    async def list(self, owner=None, limit=50):
        def op(db):
            if owner is None:
                rows = db.execute("SELECT body FROM research_run").fetchall()
            else:
                rows = db.execute("SELECT body FROM research_run WHERE owner=? ORDER BY rowid DESC LIMIT ?", (owner, limit)).fetchall()
            return [json.loads(row[0]) for row in rows]

        return await self.call(op)

    async def mutate(self, run_id, fn):
        def op(db):
            row = db.execute("SELECT body FROM research_run WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise ResearchError("NOT_FOUND", "研究任务不存在", recoverable=False)
            run = json.loads(row[0])
            fn(run)
            run["updated_at"] = utcnow()
            db.execute("UPDATE research_run SET body=? WHERE id=?", (dumps(run), run_id))
            return run

        return await self.call(op)

    async def patch(self, run_id, **values):
        return await self.mutate(run_id, lambda run: run.update(values))

    async def append_message(self, run_id, message):
        """Persist a conversation projection once, including across node replay."""

        def append(run):
            messages = run.setdefault("conversation", [])
            if not any(item["id"] == message["id"] for item in messages):
                messages.append(message)

        return await self.mutate(run_id, append)

    async def reserve(self, run_id, tool_calls=0, model_tokens=0):
        def change(run):
            if run.get("cancel_requested") or run.get("status") == "CANCELLED":
                raise ResearchError("RUN_CANCELLED", "研究已取消，不再启动新调用", recoverable=False)
            for key, delta, maximum in [("tool_calls", tool_calls, "max_tool_calls"), ("model_tokens", model_tokens, "max_model_tokens")]:
                ceiling = run["budget"][maximum]
                if ceiling is not None and run["usage"][key] + delta > ceiling:
                    raise ResearchError("BUDGET_EXHAUSTED", f"预算已用尽: {maximum}", recoverable=False)
                run["usage"][key] += delta

        return await self.mutate(run_id, change)

    async def event(self, run_id, kind, data=None, key=None):
        def op(db):
            if key:
                previous = db.execute("SELECT body FROM research_event WHERE run_id=? AND key=?", (run_id, key)).fetchone()
                if previous:
                    return json.loads(previous[0])
            seq = db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM research_event WHERE run_id=?", (run_id,)).fetchone()[0]
            event = {"seq": seq, "type": kind, "run_id": run_id, "at": utcnow(), "data": data or {}}
            db.execute("INSERT INTO research_event VALUES (?,?,?,?)", (run_id, seq, key or f"event-{seq}", dumps(event)))
            return event

        return await self.call(op)

    async def events(self, run_id, after=0, limit=200):
        return await self.call(lambda db: [json.loads(row[0]) for row in db.execute("SELECT body FROM research_event WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?", (run_id, after, limit))])

    async def favicon(self, domain):
        """Shared site-icon cache; icons are public and not tied to a run."""

        def op(db):
            row = db.execute("SELECT content_type, body, fetched_at FROM research_favicon WHERE domain=?", (domain,)).fetchone()
            return {"domain": domain, "content_type": row[0], "body": row[1], "fetched_at": row[2]} if row else None

        return await self.call(op)

    async def save_favicon(self, value):
        def op(db):
            db.execute(
                "INSERT INTO research_favicon VALUES (?,?,?,?) ON CONFLICT(domain) DO UPDATE SET content_type=excluded.content_type, body=excluded.body, fetched_at=excluded.fetched_at",
                (value["domain"], value["content_type"], value["body"], value["fetched_at"]),
            )

        await self.call(op)

    async def cached(self, run_id, key):
        def op(db):
            row = db.execute("SELECT body FROM research_cache WHERE run_id=? AND key=?", (run_id, key)).fetchone()
            return json.loads(row[0]) if row else None

        return await self.call(op)

    async def record_call(self, run_id, call_id, details, sources=()):
        """Link calls and observed sources in one transaction, replay-safe."""

        def op(db):
            old = db.execute("SELECT body FROM research_tool_call WHERE run_id=? AND id=?", (run_id, call_id)).fetchone()
            call = {**(json.loads(old[0]) if old else {}), "id": call_id, **details}
            if sources:
                call["source_ids"] = [source["id"] for source in sources]
                call["domains"] = sorted({source["domain"] for source in sources if source.get("domain")})
            db.execute("INSERT INTO research_tool_call VALUES (?,?,?) ON CONFLICT(run_id,id) DO UPDATE SET body=excluded.body", (run_id, call_id, dumps(call)))
            for source in sources:
                old = db.execute("SELECT body FROM research_source WHERE run_id=? AND id=?", (run_id, source["id"])).fetchone()
                previous = json.loads(old[0]) if old else {}
                merged = {**source, **previous}
                merged["call_ids"] = list(dict.fromkeys([*previous.get("call_ids", []), call_id]))
                if source.get("status") == "read":
                    merged.update(status="read", excerpt=source["excerpt"], document_hash=source.get("document_hash"))
                if source.get("title_observed") and (source.get("status") == "read" or previous.get("status") != "read"):
                    merged.update(title=source["title"], title_observed=True)
                db.execute("INSERT INTO research_source VALUES (?,?,?) ON CONFLICT(run_id,id) DO UPDATE SET body=excluded.body", (run_id, source["id"], dumps(merged)))
            return call

        return await self.call(op)

    async def sources(self, run_id):
        return await self.call(lambda db: [json.loads(row[0]) for row in db.execute("SELECT body FROM research_source WHERE run_id=? ORDER BY rowid", (run_id,))])

    async def calls(self, run_id):
        return await self.call(lambda db: [json.loads(row[0]) for row in db.execute("SELECT body FROM research_tool_call WHERE run_id=? ORDER BY rowid", (run_id,))])

    async def activity_events(self, run_id, limit=10000):
        """Lifecycle and activity events without trace payloads or metering."""
        return await self.call(
            lambda db: [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT body FROM research_event WHERE run_id=? AND json_extract(body, '$.type') NOT LIKE 'trace.%' AND json_extract(body, '$.type') NOT LIKE 'usage.%' ORDER BY seq LIMIT ?",
                    (run_id, limit),
                )
            ]
        )

    async def trace_events(self, run_id, after=0, limit=100):
        """Page trace records in SQL, not after limiting the mixed event stream."""
        return await self.call(lambda db: [json.loads(row[0]) for row in db.execute("SELECT body FROM research_event WHERE run_id=? AND seq>? AND json_extract(body, '$.type') LIKE 'trace.%' ORDER BY seq LIMIT ?", (run_id, after, limit))])

    async def reconcile_trace(self, run_id):
        """Append explicit interruption records beneath already-ended parents.

        This never guesses that a live root has stopped and never rewrites past
        events. The actual interruption time is unknown, so duration stays null.
        Tool status and its terminal activity are committed with the trace.
        """

        def op(db):
            rows = db.execute("SELECT body FROM research_event WHERE run_id=? AND json_extract(body, '$.type') IN ('trace.started','trace.ended') ORDER BY seq", (run_id,))
            started, ended = {}, set()
            for row in rows:
                event = json.loads(row[0])
                span = event["data"]
                if event["type"] == "trace.started":
                    started[span["span_id"]] = span
                else:
                    ended.add(span["span_id"])
            orphaned = []
            for span_id, span in started.items():
                if span_id in ended:
                    continue
                parent, visited = span.get("parent_span_id"), {span_id}
                while parent and parent not in visited:
                    if parent in ended:
                        orphaned.append(span)
                        break
                    visited.add(parent)
                    parent = started.get(parent, {}).get("parent_span_id")
            if not orphaned:
                return 0
            seq = db.execute("SELECT COALESCE(MAX(seq),0) FROM research_event WHERE run_id=?", (run_id,)).fetchone()[0]
            at = utcnow()

            def append(kind, data, key):
                nonlocal seq
                seq += 1
                event = {"seq": seq, "type": kind, "run_id": run_id, "at": at, "data": data}
                db.execute("INSERT INTO research_event VALUES (?,?,?,?)", (run_id, seq, key, dumps(event)))

            for span in orphaned:
                span_id = span["span_id"]
                data = {key: span.get(key) for key in ("trace_id", "span_id", "parent_span_id", "name", "kind")}
                data.update(status="cancelled", reconciled=True, duration_ms=None, error_type="InterruptedTrace")
                append("trace.ended", data, "reconciled-trace:" + span_id)
                if span.get("kind") == "tool":
                    row = db.execute("SELECT body FROM research_tool_call WHERE run_id=? AND id=?", (run_id, span_id)).fetchone()
                    if row:
                        call = json.loads(row[0])
                        if call.get("status") == "running":
                            call.update(status="error", ended_at=at, duration_ms=None, reconciled=True, error_type="InterruptedTrace")
                            db.execute("UPDATE research_tool_call SET body=? WHERE run_id=? AND id=?", (dumps(call), run_id, span_id))
                            append("activity.tool.completed", call, "reconciled-tool:" + span_id)
            return len(orphaned)

        return await self.call(op)

    async def cache(self, run_id, key, body):
        await self.call(lambda db: db.execute("INSERT OR IGNORE INTO research_cache VALUES (?,?,?)", (run_id, key, dumps(body))).rowcount)

    async def unit(self, run_id, unit_id, input_hash):
        def op(db):
            row = db.execute("SELECT input_hash,result FROM research_unit WHERE run_id=? AND id=?", (run_id, unit_id)).fetchone()
            if row and row[0] != input_hash:
                raise ResearchError("UNIT_CHANGED", "已执行单元的输入发生变化，需创建新任务", recoverable=False)
            return json.loads(row[1]) if row else None

        return await self.call(op)

    async def completed_unit(self, run_id, unit_id):
        """Read provenance for a declared dependency within the same run/cycle.

        Dispatch owns input-hash validation; this lookup never admits work or
        treats a result from another run as an interchangeable dependency.
        """

        def op(db):
            row = db.execute("SELECT result FROM research_unit WHERE run_id=? AND id=?", (run_id, unit_id)).fetchone()
            return json.loads(row[0]) if row else None

        return await self.call(op)

    async def save_unit(self, run_id, unit_id, input_hash, result):
        await self.call(lambda db: db.execute("INSERT OR IGNORE INTO research_unit VALUES (?,?,?,?)", (run_id, unit_id, input_hash, dumps(result))).rowcount)

    async def save_pool(self, run_id, pool, gaps):
        def op(db):
            for eid, evidence in pool.items():
                db.execute("INSERT OR REPLACE INTO research_evidence VALUES (?,?,?)", (run_id, eid, dumps(evidence)))
            for gap in gaps:
                db.execute("INSERT OR REPLACE INTO research_gap VALUES (?,?,?)", (run_id, gap["gap_id"], dumps(gap)))

        await self.call(op)

    async def save_report(self, run_id, version, body):
        await self.call(lambda db: db.execute("INSERT OR REPLACE INTO research_report VALUES (?,?,?)", (run_id, version, dumps(body))).rowcount)

    async def publish_report(self, run_id, publication_key, body):
        """Commit version, conversation, terminal state and event together.

        The content-addressed publication key fences node replay. Native graph
        checkpoints may lag this transaction without duplicating a report.
        """

        def op(db):
            row = db.execute("SELECT body FROM research_run WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise ResearchError("NOT_FOUND", "研究任务不存在", recoverable=False)
            run = json.loads(row[0])
            if run.get("cancel_requested") or run.get("status") == "CANCELLED":
                raise ResearchError("RUN_CANCELLED", "研究已取消，不发布报告", recoverable=False)
            key = "publication:" + publication_key
            existing = db.execute("SELECT body FROM research_cache WHERE run_id=? AND key=?", (run_id, key)).fetchone()
            if existing:
                published = json.loads(existing[0])
            else:
                version = db.execute("SELECT COALESCE(MAX(version),0)+1 FROM research_report WHERE run_id=?", (run_id,)).fetchone()[0]
                published = {**body, "version": version}
                at = utcnow()
                db.execute("INSERT INTO research_report VALUES (?,?,?)", (run_id, version, dumps(published)))
                title = published.get("title") or published["report"]["title"]
                run.setdefault("conversation", []).append({"id": f"report-{version}", "role": "assistant", "kind": "report", "text": title, "report": published, "cycle": run.get("cycle", 0), "at": at})
                seq = db.execute("SELECT COALESCE(MAX(seq),0)+1 FROM research_event WHERE run_id=?", (run_id,)).fetchone()[0]
                event = {"seq": seq, "type": "report.completed", "run_id": run_id, "at": at, "data": {"version": version, "title": title, "limitations": published["limitations"]}}
                db.execute("INSERT INTO research_event VALUES (?,?,?,?)", (run_id, seq, f"report-completed-{version}", dumps(event)))
                db.execute("INSERT INTO research_cache VALUES (?,?,?)", (run_id, key, dumps(published)))
            run.update(report=published, status="COMPLETED", error=None, updated_at=utcnow())
            db.execute("UPDATE research_run SET body=? WHERE id=?", (dumps(run), run_id))
            return published

        return await self.call(op)


class ProcessLock:
    """Fail closed on accidental multiple workers sharing the same local runtime."""

    def __init__(self, path):
        self.path, self.file = path, None

    def acquire(self):
        import os

        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            self.path.parent.chmod(0o700)
        self.file = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt

                self.file.seek(0)
                self.file.write(b"0")
                self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            self.file = None
            raise RuntimeError("DeepResearch SQLite runtime requires exactly one worker") from None

    def release(self):
        if self.file:
            self.file.close()
            self.file = None
