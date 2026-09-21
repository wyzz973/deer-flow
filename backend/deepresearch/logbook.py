"""Everything one research recorded, as one file — and the only way to delete it.

The records of a run are spread over a dozen tables on purpose: metrics are
grouped and queried, bodies are content-addressed and shared. That is right for
the product and wrong for someone who wants to read what happened, so ``export``
joins them back together in time order and resolves every body, leaving a file
that needs nothing else to be understood.

``prune`` is the counterpart. Research keeps what it recorded until somebody
says otherwise, so this is the one delete path, and it never runs on its own
without a configured retention.

    python -m deepresearch.logbook export --data-dir <dir> --run latest
    python -m deepresearch.logbook prune  --data-dir <dir> --older-than 30d [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# One line per record, in the order the run produced them. The first two say
# what was asked and under which settings; the rest is what actually happened.
ORDER = ("run", "settings", "event", "model_call", "tool_call", "wire_call", "agent_run", "unit", "evidence", "report")


def _at(record):
    return record.get("started_at") or record.get("at") or record.get("created_at") or ""


async def export(store, run_id, *, compact=False, pricing=None):
    """Yield the whole run as JSON lines. Bodies are already resolved."""
    run = await store.get(run_id)
    if run is None:
        raise LookupError(run_id)
    yield _line({"record": "run", **run})
    profile = (run.get("profile") or {}).get("hash")
    snapshot = await store.snapshot(profile) if profile else None
    if snapshot is not None:
        yield _line({"record": "settings", "run_id": run_id, **snapshot})

    exchanges = {item["id"]: item for item in await store.tool_exchanges(run_id)}
    calls = []
    for row in await store.calls(run_id):
        # The metrics row and the bodies are one thing to a reader.
        body = exchanges.pop(row.get("id"), None) or {}
        calls.append({"record": "tool_call", "run_id": run_id, **{key: value for key, value in body.items() if key != "id"}, **row})
    calls += [{"record": "tool_call", "run_id": run_id, **body} for body in exchanges.values()]

    models = []
    for row in await store.model_calls(run_id):
        detail = await store.llm_exchange(run_id, row.get("id"))
        models.append({"record": "model_call", "run_id": run_id, **(detail or {}), **row, "audited": detail is not None})

    wires = []
    for row in await store.wire_calls(run_id):
        wires.append({"record": "wire_call", "run_id": run_id, **(await store.wire_call(run_id, row["id"]) or row)})

    events = [{"record": "event", **item} for item in await store.activity_events(run_id, limit=1000000)]
    if compact:
        # The span payloads duplicate the model and tool records above.
        events = [item if not str(item.get("type", "")).startswith("trace.") else {**item, "data": {key: value for key, value in (item.get("data") or {}).items() if key != "payload"}} for item in events]

    for record in sorted([*events, *models, *calls, *wires], key=_at):
        yield _line(record)
    for item in await store.agent_runs(run_id):
        yield _line({"record": "agent_run", "run_id": run_id, **item})
    for item in await store.unit_results(run_id):
        yield _line({"record": "unit", "run_id": run_id, **item})
    for item in await store.evidences(run_id):
        yield _line({"record": "evidence", "run_id": run_id, **item})
    for version in await store.reports(run_id):
        yield _line({"record": "report", "run_id": run_id, **version})


def _line(record):
    return json.dumps(record, ensure_ascii=False, default=str)


def older_than(value, now=None):
    """`30d`, `12h`, `2026-01-01` or an ISO timestamp -> the cutoff to prune before."""
    match = re.fullmatch(r"(\d+)\s*([dhw])", value.strip(), re.I)
    if match:
        amount, unit = int(match.group(1)), match.group(2).lower()
        delta = {"d": timedelta(days=amount), "h": timedelta(hours=amount), "w": timedelta(weeks=amount)}[unit]
        return ((now or datetime.now(UTC)) - delta).isoformat()
    moment = datetime.fromisoformat(value)
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).isoformat()


def resolve(store_runs, reference):
    """A run id, an id prefix, a page URL, a thread id, or 'latest'."""
    reference = (reference or "").strip().rstrip("/").rsplit("/", 1)[-1]
    if reference == "latest":
        return max(store_runs, key=lambda item: item.get("created_at") or "")["run_id"]
    for item in store_runs:
        if reference in {item["run_id"], item.get("thread_id")} or item["run_id"].startswith(reference):
            return item["run_id"]
    raise LookupError(reference)


def main(argv=None):
    from .store import Store

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    # Repeated on both subcommands so it reads naturally either side of them.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--data-dir", required=True, type=Path, help="Directory containing research.sqlite3")
    parser.add_argument("--data-dir", type=Path, help=argparse.SUPPRESS)
    commands = parser.add_subparsers(dest="command", required=True)
    shipping = commands.add_parser("export", parents=[common], help="Write one run's complete records as JSON lines")
    shipping.add_argument("--run", required=True, help="Run id, id prefix, page URL, thread id, or 'latest'")
    shipping.add_argument("--out", type=Path, help="Write here instead of stdout")
    shipping.add_argument("--compact", action="store_true", help="Leave out span payloads, which duplicate the call records")
    cleaning = commands.add_parser("prune", parents=[common], help="Delete runs older than a cutoff, and reclaim the space")
    cleaning.add_argument("--older-than", required=True, help="30d, 12h, 2w or a date")
    cleaning.add_argument("--dry-run", action="store_true", help="Report what would go without writing")
    args = parser.parse_args(argv)
    database = args.data_dir / "research.sqlite3"
    if not database.is_file():
        parser.error(f"{database} does not exist")

    async def go():
        store = Store(database)
        await store.start()
        if args.command == "prune":
            report = await store.prune(older_than(args.older_than), dry_run=args.dry_run)
            reclaimed = report.get("reclaimed_bytes")
            print(f"{'would delete' if args.dry_run else 'deleted'} {len(report['runs'])} run(s), {report.get('rows', 0)} rows" + (f", reclaimed {reclaimed / 1e6:.1f}MB" if reclaimed else ""))
            for run_id in report["runs"]:
                print("  " + run_id)
            return
        run_id = resolve(await store.list(), args.run)
        out = args.out.open("w", encoding="utf-8") if args.out else sys.stdout
        try:
            count = 0
            async for line in export(store, run_id, compact=args.compact):
                out.write(line + "\n")
                count += 1
        finally:
            if args.out:
                out.close()
        if args.out:
            print(f"{args.out}  {count} records  {args.out.stat().st_size / 1e6:.1f}MB")

    asyncio.run(go())
    return 0


if __name__ == "__main__":
    sys.exit(main())
