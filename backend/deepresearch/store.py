"""Single-process SQLite store. Each operation runs off the event loop.

Success caches are durable independently of LangGraph checkpoints. A remote call
that succeeds immediately before process death is still at-least-once, not exactly-once.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

from .contracts import ResearchError, utcnow


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


# Tokens a report needs at the least. A measured three-step report took eight
# calls (outline, six sections, summary) and about 145k tokens; a compact one
# about half. Less than this cannot hold even the parallel section calls.
REPORT_RESERVE_FLOOR = 60000


def report_reserve(run) -> int:
    """Tokens kept back so a run that spends its research budget can still write.

    A fifth of the ceiling, never less than REPORT_RESERVE_FLOOR and never more
    than half; an unlimited ceiling reserves nothing.
    """
    ceiling = (run.get("budget") or {}).get("max_model_tokens")
    if not ceiling:
        return 0
    return min(ceiling // 2, max(ceiling // 5, REPORT_RESERVE_FLOOR))


# Output tokens reserved per call before its reported usage replaces the
# estimate: a research turn is a tool call or a short note, and a report call
# (outline, one section, the summary) measured at most about 5k.
RESEARCH_TURN_OUTPUT = 4096
REPORT_CALL_OUTPUT = 8192


def research_tokens_left(run) -> int | None:
    """Model tokens research may still spend before the report reserve, or None."""
    ceiling = (run.get("budget") or {}).get("max_model_tokens")
    if ceiling is None:
        return None
    return max(0, ceiling - report_reserve(run) - (run.get("usage") or {}).get("model_tokens", 0))


def research_spent(run) -> bool:
    """Whether research can no longer afford a turn, so supplementing is futile.

    A balance that cannot cover a turn (its output plus a prompt of similar
    size) is spent, and so is a run where a step already failed to reserve one:
    the next step's prompt is no smaller.
    """
    if "RESEARCH_BUDGET_SPENT" in (run.get("unit_failures") or {}).values():
        return True
    left = research_tokens_left(run)
    return left is not None and left < 2 * RESEARCH_TURN_OUTPUT


def elapsed_seconds(run) -> float:
    """Execution time charged so far, including the drive that is running now.

    ``usage.elapsed_seconds`` only grows when a drive ends; ``drive_started_at``
    (wall clock, set by the service) covers the one in progress. Time spent
    waiting for the owner to confirm a plan is never charged.
    """
    spent = (run.get("usage") or {}).get("elapsed_seconds", 0) or 0
    started = run.get("drive_started_at")
    return spent + (max(0.0, time.time() - started) if isinstance(started, (int, float)) else 0.0)


def report_time_reserve(run, settings=None) -> float:
    """Seconds of the time budget kept back for writing the report."""
    ceiling = (run.get("budget") or {}).get("max_elapsed_seconds")
    if not ceiling:
        return 0.0
    configured = getattr(settings, "report_time_reserve_seconds", None)
    reserve = configured if configured is not None else min(900, max(120, ceiling // 4))
    return float(min(reserve, ceiling // 2))


def research_seconds_left(run, settings=None) -> float | None:
    """Seconds research may still take before the report reserve, or None when unlimited.

    The time budget winds research down the way the token budget does: steps
    are told to wrap up, supplementing stops and the report is still written.
    Ending the whole run at the ceiling threw away everything a slow model had
    already researched.
    """
    ceiling = (run.get("budget") or {}).get("max_elapsed_seconds")
    if ceiling is None:
        return None
    return max(0.0, ceiling - report_time_reserve(run, settings) - elapsed_seconds(run))


# A research step needs at least this long to do anything useful.
MIN_UNIT_SECONDS = 20


def research_time_spent(run, settings=None) -> bool:
    left = research_seconds_left(run, settings)
    return left is not None and left < MIN_UNIT_SECONDS


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
            CREATE TABLE IF NOT EXISTS research_model_call (
              run_id TEXT NOT NULL REFERENCES research_run(id), id TEXT NOT NULL,
              body TEXT NOT NULL, PRIMARY KEY(run_id,id));
            CREATE TABLE IF NOT EXISTS research_agent_run (
              run_id TEXT NOT NULL REFERENCES research_run(id), id TEXT NOT NULL,
              body TEXT NOT NULL, PRIMARY KEY(run_id,id));
            CREATE TABLE IF NOT EXISTS research_llm_exchange (
              run_id TEXT NOT NULL REFERENCES research_run(id), id TEXT NOT NULL,
              body TEXT NOT NULL, PRIMARY KEY(run_id,id));
            CREATE TABLE IF NOT EXISTS research_llm_blob (
              run_id TEXT NOT NULL REFERENCES research_run(id), hash TEXT NOT NULL,
              body BLOB NOT NULL, PRIMARY KEY(run_id,hash));
            CREATE TABLE IF NOT EXISTS research_profile (
              id TEXT PRIMARY KEY, version INTEGER NOT NULL, body TEXT NOT NULL,
              updated_at TEXT NOT NULL, updated_by TEXT);
            CREATE TABLE IF NOT EXISTS research_profile_history (
              id TEXT NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL,
              updated_at TEXT NOT NULL, updated_by TEXT, PRIMARY KEY(id,version));
            CREATE TABLE IF NOT EXISTS research_profile_snapshot (
              hash TEXT PRIMARY KEY, body BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS research_secret (
              name TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT);
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

    async def reserve(self, run_id, tool_calls=0, model_tokens=0, purpose="report"):
        """Account a model or tool call against the run budget.

        Research keeps a reserve so the report can still be written: a
        ``purpose="research"`` call stops at the ceiling minus that reserve and
        reports ``RESEARCH_BUDGET_SPENT``, which degrades its own step instead
        of ending the run. Writing the report may use the whole ceiling.
        """

        def change(run):
            if run.get("cancel_requested") or run.get("status") == "CANCELLED":
                raise ResearchError("RUN_CANCELLED", "研究已取消，不再启动新调用", recoverable=False)
            reserve = report_reserve(run) if purpose == "research" else 0
            for key, delta, maximum in [("tool_calls", tool_calls, "max_tool_calls"), ("model_tokens", model_tokens, "max_model_tokens")]:
                ceiling = run["budget"][maximum]
                if ceiling is None:
                    run["usage"][key] += delta
                    continue
                keep = reserve if key == "model_tokens" else 0
                if run["usage"][key] + delta > max(0, ceiling - keep):
                    if keep and run["usage"][key] + delta <= ceiling:
                        raise ResearchError("RESEARCH_BUDGET_SPENT", "研究预算已用尽，剩余额度留给报告写作", recoverable=False)
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

    async def last_event_seq(self, run_id):
        return await self.call(lambda db: db.execute("SELECT COALESCE(MAX(seq),0) FROM research_event WHERE run_id=?", (run_id,)).fetchone()[0])

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

    async def _merge_record(self, table, run_id, record_id, details):
        def op(db):
            old = db.execute(f"SELECT body FROM {table} WHERE run_id=? AND id=?", (run_id, record_id)).fetchone()
            record = {**(json.loads(old[0]) if old else {}), "id": record_id, **details}
            db.execute(f"INSERT INTO {table} VALUES (?,?,?) ON CONFLICT(run_id,id) DO UPDATE SET body=excluded.body", (run_id, record_id, dumps(record)))
            return record

        return await self.call(op)

    async def _records(self, table, run_id):
        return await self.call(lambda db: [json.loads(row[0]) for row in db.execute(f"SELECT body FROM {table} WHERE run_id=? ORDER BY rowid", (run_id,))])

    async def record_model_call(self, run_id, call_id, details):
        """Metrics for one model request, merged across its start and end."""
        return await self._merge_record("research_model_call", run_id, call_id, details)

    async def model_calls(self, run_id):
        return await self._records("research_model_call", run_id)

    async def model_call(self, run_id, call_id):
        def op(db):
            row = db.execute("SELECT body FROM research_model_call WHERE run_id=? AND id=?", (run_id, call_id)).fetchone()
            return json.loads(row[0]) if row else None

        return await self.call(op)

    async def record_agent_run(self, run_id, execution_id, details):
        """Metrics for one native subagent execution."""
        return await self._merge_record("research_agent_run", run_id, execution_id, details)

    async def agent_runs(self, run_id):
        return await self._records("research_agent_run", run_id)

    async def record_llm_request(self, run_id, call_id, details, messages, tools):
        """Store one model request; identical messages across agent turns are stored once."""
        from .audit import encode

        def op(db):
            hashes = []
            for message in messages:
                digest, blob = encode(message)
                db.execute("INSERT OR IGNORE INTO research_llm_blob VALUES (?,?,?)", (run_id, digest, blob))
                hashes.append(digest)
            tools_hash = None
            if tools:
                tools_hash, blob = encode(tools)
                db.execute("INSERT OR IGNORE INTO research_llm_blob VALUES (?,?,?)", (run_id, tools_hash, blob))
            old = db.execute("SELECT body FROM research_llm_exchange WHERE run_id=? AND id=?", (run_id, call_id)).fetchone()
            record = {**(json.loads(old[0]) if old else {}), **details, "id": call_id, "message_hashes": hashes, "tools_hash": tools_hash}
            db.execute("INSERT INTO research_llm_exchange VALUES (?,?,?) ON CONFLICT(run_id,id) DO UPDATE SET body=excluded.body", (run_id, call_id, dumps(record)))

        await self.call(op)

    async def record_llm_response(self, run_id, call_id, details):
        return await self._merge_record("research_llm_exchange", run_id, call_id, details)

    async def llm_exchanges(self, run_id):
        """Call summaries in request order, without message bodies."""

        def op(db):
            items = []
            for (body,) in db.execute("SELECT body FROM research_llm_exchange WHERE run_id=? ORDER BY rowid", (run_id,)):
                record = json.loads(body)
                record.pop("message_hashes", None)
                record.pop("response", None)
                items.append(record)
            return items

        return await self.call(op)

    async def llm_exchange(self, run_id, call_id):
        """One complete request/response, plus how much it repeats the previous turn."""
        from .audit import decode, shared_prefix

        def op(db):
            rows = db.execute("SELECT rowid, body FROM research_llm_exchange WHERE run_id=? AND id=?", (run_id, call_id)).fetchone()
            if rows is None:
                return None
            rowid, record = rows[0], json.loads(rows[1])
            hashes = record.pop("message_hashes", []) or []
            wanted = list(dict.fromkeys([*hashes, *([record["tools_hash"]] if record.get("tools_hash") else [])]))
            blobs = {}
            for start in range(0, len(wanted), 500):
                chunk = wanted[start : start + 500]
                marks = ",".join("?" for _ in chunk)
                for digest, blob in db.execute(f"SELECT hash, body FROM research_llm_blob WHERE run_id=? AND hash IN ({marks})", (run_id, *chunk)):
                    blobs[digest] = decode(blob)
            record["messages"] = [blobs.get(digest) for digest in hashes]
            record["message_hashes"] = hashes
            record["tools"] = blobs.get(record.get("tools_hash")) if record.get("tools_hash") else None
            # The previous request of the same agent loop (or conversion retry
            # sequence) lets a reader see only what this turn added.
            group = record.get("group")
            previous = None
            if group:
                for (body,) in db.execute("SELECT body FROM research_llm_exchange WHERE run_id=? AND rowid<? ORDER BY rowid DESC", (run_id, rowid)):
                    candidate = json.loads(body)
                    if candidate.get("group") == group:
                        previous = candidate
                        break
            record["previous_call_id"] = previous["id"] if previous else None
            record["repeated_prefix"] = shared_prefix(previous.get("message_hashes") or [], hashes) if previous else 0
            # Engines may insert per-turn messages before the history, so compare sets too.
            seen = set(previous.get("message_hashes") or []) if previous else set()
            record["new_message_indexes"] = [index for index, digest in enumerate(hashes) if digest not in seen] if previous else list(range(len(hashes)))
            return record

        return await self.call(op)

    async def profile(self, profile_id="default"):
        def op(db):
            row = db.execute("SELECT version, body, updated_at, updated_by FROM research_profile WHERE id=?", (profile_id,)).fetchone()
            return {"version": row[0], "overrides": json.loads(row[1]), "updated_at": row[2], "updated_by": row[3]} if row else None

        return await self.call(op)

    async def save_profile(self, overrides, expected_version, user=None, profile_id="default"):
        """Store settings-page overrides; a stale editor cannot overwrite a newer version."""

        def op(db):
            row = db.execute("SELECT version FROM research_profile WHERE id=?", (profile_id,)).fetchone()
            version = row[0] if row else 0
            if expected_version != version:
                raise ResearchError("PROFILE_VERSION", "研究设置已被其他人修改，请刷新后再保存", recoverable=False)
            record = {"version": version + 1, "overrides": overrides, "updated_at": utcnow(), "updated_by": user}
            body = dumps(overrides)
            db.execute(
                "INSERT INTO research_profile VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET version=excluded.version, body=excluded.body, updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                (profile_id, record["version"], body, record["updated_at"], user),
            )
            db.execute("INSERT INTO research_profile_history VALUES (?,?,?,?,?)", (profile_id, record["version"], body, record["updated_at"], user))
            return record

        return await self.call(op)

    async def profile_history(self, profile_id="default", limit=50):
        def op(db):
            rows = db.execute("SELECT version, body, updated_at, updated_by FROM research_profile_history WHERE id=? ORDER BY version DESC LIMIT ?", (profile_id, limit))
            return [{"version": row[0], "overrides": json.loads(row[1]), "updated_at": row[2], "updated_by": row[3]} for row in rows]

        return await self.call(op)

    async def secrets(self):
        """Saved credential values; only the service reads them, never an API response."""
        return await self.call(lambda db: dict(db.execute("SELECT name, value FROM research_secret").fetchall()))

    async def secret_names(self):
        return await self.call(lambda db: [{"name": row[0], "updated_at": row[1], "updated_by": row[2]} for row in db.execute("SELECT name, updated_at, updated_by FROM research_secret ORDER BY name")])

    async def save_secret(self, name, value, user=None):
        await self.call(
            lambda db: (
                db.execute(
                    "INSERT INTO research_secret VALUES (?,?,?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at, updated_by=excluded.updated_by",
                    (name, value, utcnow(), user),
                ).rowcount
            )
        )

    async def delete_secret(self, name):
        return await self.call(lambda db: db.execute("DELETE FROM research_secret WHERE name=?", (name,)).rowcount)

    async def save_snapshot(self, ref, body):
        import zlib

        await self.call(lambda db: db.execute("INSERT OR IGNORE INTO research_profile_snapshot VALUES (?,?)", (ref, zlib.compress(dumps(body).encode("utf-8"), 6))).rowcount)

    async def snapshot(self, ref):
        import zlib

        def op(db):
            row = db.execute("SELECT body FROM research_profile_snapshot WHERE hash=?", (ref,)).fetchone()
            return json.loads(zlib.decompress(row[0]).decode("utf-8")) if row else None

        return await self.call(op)

    async def unit_results(self, run_id):
        return await self.call(lambda db: [{"id": row[0], "result": json.loads(row[1])} for row in db.execute("SELECT id, result FROM research_unit WHERE run_id=? ORDER BY rowid", (run_id,))])

    async def span_timings(self, run_id, kinds=("workflow", "node")):
        """Span timings of the given kinds without payloads; open spans have no duration."""
        marks = ",".join("?" for _ in kinds)

        def op(db):
            rows = db.execute(
                "SELECT json_extract(body, '$.type'), json_extract(body, '$.at'), json_extract(body, '$.data.span_id'), json_extract(body, '$.data.kind'), "
                "json_extract(body, '$.data.name'), json_extract(body, '$.data.status'), json_extract(body, '$.data.duration_ms') "
                f"FROM research_event WHERE run_id=? AND json_extract(body, '$.type') IN ('trace.started', 'trace.ended') AND json_extract(body, '$.data.kind') IN ({marks}) ORDER BY seq",
                (run_id, *kinds),
            )
            spans = {}
            for kind, at, span_id, span_kind, name, status, duration in rows:
                entry = spans.setdefault(span_id, {"kind": span_kind, "name": name, "status": "running", "duration_ms": None, "at": at})
                if kind == "trace.ended":
                    entry.update(status=status, duration_ms=duration, at=at)
            return list(spans.values())

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
