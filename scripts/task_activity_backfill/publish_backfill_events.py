#!/usr/bin/env python3
"""Publish backfill TaskActivityEvents to Kafka.

Pulls every thread whose root run starts in `[--start-time, --end-time)`
across the projects in `--source-projects` (or
`$RESIDENT_LANGSMITH_PROJECTS`), runs each through the replay dispatch
(concurrently — see `--workers`), then publishes each event with an
explicit Kafka message timestamp = event_time so downstream day-window
math sees the original conversation time.

`--source langsmith` (default) pulls from the LangSmith API (rate-limit
prone, ~3 s/thread serial). `--source db_s3` reads from the
`alpha_agentic_evals` Postgres `traces` table for `(thread_id, raw_data_uri)`
discovery and downloads each LangSmith Run JSON blob from S3 in parallel
(no rate limit, ~50 ms/thread). The DB+S3 path requires `EVAL_DB_URL` in
the env file and a working AWS SSO session (`--aws-profile`, default
`alpha`). Coverage: alpha/beta only, ≥ 2026-04-14 (S3 archival start).

The target Kafka topic comes from `$KAFKA_TASK_ACTIVITY_TOPIC` in the
env file. Topics ending in `-qa` / `-sat` are non-prod; anything else
requires `--confirm-prod`.

`--limit N` is an optional refuse-on-exceed safety guard: when set, the
publisher exits non-zero before any Kafka call if discovery turns up
more than N events. No silent partial publish.

`--dedup-log <path>` records every successfully-published event as a
`(task.id, activity.summary, event_time)` triple. On re-run, events
already in the log are skipped before any produce call — so retrying
a window (e.g. one day in a daily driver) doesn't double-publish to
the topic. Atomic at line level under a lock.

Usage:
  uv run scripts/task_activity_backfill/publish_backfill_events.py \\
      --env-file <env-file> --start-time 2026-04-10 --end-time 2026-04-11
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent))
import replay_trace_activity_events as replay  # noqa: E402
from env_loader import load_env_file  # noqa: E402
from property_lookup import PropertyTimezoneLookup  # noqa: E402

HEARTBEAT_INTERVAL_S = 600.0
RATE_LIMIT_MAX_RETRIES = 6  # 1+2+4+8+16+32 = 63s max per thread before giving up


def parse_utc_time(text: str) -> datetime:
    """Accept ISO 8601 (with or without explicit tz) or `YYYY-MM-DD` date-only.

    Bare-date input is interpreted as midnight UTC. Naive ISO is also
    coerced to UTC so the bound is unambiguous on the wire.
    """
    s = (text or "").strip()
    if not s:
        raise argparse.ArgumentTypeError("empty timestamp")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid UTC time {text!r}: expected ISO 8601 (e.g. 2026-04-10T00:00:00Z) or YYYY-MM-DD (e.g. 2026-04-10)"
        ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env-file", type=Path, required=True, help="Dotenv to load.")
    parser.add_argument(
        "--source-projects",
        nargs="+",
        default=None,
        help="LangSmith project names to source threads from. "
        "Falls back to env $RESIDENT_LANGSMITH_PROJECTS (comma-separated).",
    )
    parser.add_argument(
        "--start-time",
        type=parse_utc_time,
        required=True,
        help="UTC window start (inclusive). ISO 8601 or YYYY-MM-DD.",
    )
    parser.add_argument(
        "--end-time",
        type=parse_utc_time,
        required=True,
        help="UTC window end (exclusive). ISO 8601 or YYYY-MM-DD.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Refuse to publish if discovery exceeds this count. Default: unlimited.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=3,
        help="Concurrent threads pulling per-thread events from LangSmith. Default: 3. "
        "Higher values hit langsmith 429 rate limits more often (publisher retries with backoff).",
    )
    parser.add_argument(
        "--dedup-log",
        type=Path,
        default=Path("data/backfill_volume/published.jsonl"),
        help="Append-only log of published (task.id, activity.summary, event_time). "
        "Existing entries are skipped on re-run. Default: data/backfill_volume/published.jsonl.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Collect & preview events but don't publish.")
    parser.add_argument(
        "--confirm-prod",
        action="store_true",
        help="Required to publish to a topic without a `-qa`/`-sat` suffix.",
    )
    parser.add_argument(
        "--source",
        choices=("langsmith", "db_s3"),
        default="langsmith",
        help="Trace source: `langsmith` (default, API) or `db_s3` "
        "(alpha_agentic_evals Postgres + S3 LangSmith Run JSON; faster, alpha/beta only, ≥2026-04-14).",
    )
    parser.add_argument(
        "--aws-profile",
        default="alpha",
        help="AWS SSO profile for S3 GETs when --source=db_s3. Default: alpha.",
    )
    return parser.parse_args()


def topic_looks_like_prod(topic: str | None) -> bool:
    """Topics ending in `-qa` or `-sat` are non-prod by repo convention.

    Anything else — including no suffix and unknown suffixes — is treated
    as prod and requires `--confirm-prod`.
    """
    if not topic:
        return False
    return not (topic.endswith("-qa") or topic.endswith("-sat"))


def resolve_source_projects(cli_value: list[str] | None) -> list[str]:
    if cli_value:
        return cli_value
    raw = os.environ.get("RESIDENT_LANGSMITH_PROJECTS", "").strip()
    if not raw:
        return []
    return [p.strip() for p in raw.split(",") if p.strip()]


def discover_threads(
    client: Any, projects: list[str], start_time: datetime, end_time: datetime
) -> list[tuple[str, str]]:
    """Return all unique (project, thread_id) pairs for root runs in [start, end).

    Uses langsmith's auto-pagination — caller doesn't loop. Per-project
    thread-id de-dup keeps the same thread from being processed twice
    when multiple turns of one conversation fall in the window.
    """
    out: list[tuple[str, str]] = []
    for proj in projects:
        runs = client.list_runs(
            project_name=proj,
            is_root=True,
            start_time=start_time,
            end_time=end_time,
            order_by=["start_time"],
        )
        seen_for_proj: set[str] = set()
        for run in runs:
            tid = (run.extra or {}).get("metadata", {}).get("thread_id")
            if not tid or tid in seen_for_proj:
                continue
            seen_for_proj.add(tid)
            out.append((proj, tid))
    return out


def _fetch_thread_runs_with_backoff(client: Any, project: str, thread_id: str) -> list[Any]:
    """Wrap `replay.fetch_thread_runs` with exponential backoff on 429.

    Langsmith rate-limits at the project level; with multiple workers we
    hit it regularly. Without retry, the publisher silently drops the
    thread (caught one level up). Backoff: 1, 2, 4, 8, 16, 32 seconds.
    """
    from langsmith.utils import LangSmithRateLimitError

    for attempt in range(RATE_LIMIT_MAX_RETRIES):
        try:
            return replay.fetch_thread_runs(client, project, thread_id)
        except LangSmithRateLimitError:
            if attempt == RATE_LIMIT_MAX_RETRIES - 1:
                raise
            time.sleep(2**attempt)
    return []


def collect_events_for_thread(client: Any, project: str, thread_id: str, lookup: PropertyTimezoneLookup) -> list[dict]:
    runs = _fetch_thread_runs_with_backoff(client, project, thread_id)
    fallback_ctx, ctx_source = replay.resolve_thread_ctx(runs, thread_id)
    if ctx_source in ("missing", "no-interaction"):
        return []
    tools = [r for r in runs if r.run_type == "tool"]
    llm_runs = [r for r in runs if r.run_type == "llm"]
    root_runs = [r for r in runs if r.parent_run_id is None]
    voice_trace_ids = {str(r.trace_id) for r in tools if r.name == replay.THINKER_TOOL}
    prefetch_ids = {str(r.id) for r in runs if r.name == replay.PREFETCH_CHAIN_NAME}
    user_msg_by_trace = {str(r.id): (r.inputs or {}).get("message") if r.inputs else None for r in root_runs}

    events: list[dict] = []

    for run in tools:
        name = run.name
        inputs = run.inputs or {}
        outputs = run.outputs or {}
        if not inputs:
            continue
        if name in replay.HANDOFF_TOOLS:
            ev_list = replay.replay_handoff(name, inputs, fallback_ctx)
        elif name == replay.THINKER_TOOL:
            ev_list = replay.replay_thinker_qna(inputs, outputs, fallback_ctx)
        elif name == replay.CALL_TOOL:
            ancestors = {str(p) for p in (run.parent_run_ids or [])}
            if ancestors & prefetch_ids:
                continue
            ev_list = replay.replay_mcp_business_tool(inputs, outputs, fallback_ctx)
        elif name == replay.FACILITIES_THINKER_TOOL:
            ev_list = replay.replay_facilities_thinker(inputs, outputs, fallback_ctx)
        else:
            continue
        run_channel = replay.derive_run_channel(run, fallback_ctx)
        replay.rewrite_task_id(ev_list, thread_id, run_channel)
        replay.fill_property_timezone(ev_list, lookup)
        et = run.start_time.isoformat() if run.start_time else None
        for e in ev_list:
            events.append({"event_time": et, "event": e, "project": project, "thread_id": thread_id})

    seen_traces: set[str] = set()
    for llm in llm_runs:
        tid = str(llm.trace_id)
        if tid in voice_trace_ids or tid in seen_traces:
            continue
        parsed = replay.parse_responder_output(llm.outputs)
        if parsed is None:
            continue
        seen_traces.add(tid)
        ev_list = replay.replay_responder_output(parsed, user_msg_by_trace.get(tid), fallback_ctx)
        run_channel = replay.derive_run_channel(llm, fallback_ctx)
        replay.rewrite_task_id(ev_list, thread_id, run_channel)
        replay.fill_property_timezone(ev_list, lookup)
        et = llm.start_time.isoformat() if llm.start_time else None
        for e in ev_list:
            events.append({"event_time": et, "event": e, "project": project, "thread_id": thread_id})

    return events


def _parse_iso(text: str | None) -> datetime | None:
    """Parse an ISO timestamp from the S3 LangSmith Run JSON (handles `Z` suffix and naive)."""
    if not text:
        return None
    s = text
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _run_dict_to_obj(d: dict) -> SimpleNamespace:
    """Adapt a LangSmith Run dict (from S3 JSON) to the attribute interface extractors expect."""
    return SimpleNamespace(
        id=d.get("id"),
        run_type=d.get("run_type"),
        name=d.get("name"),
        inputs=d.get("inputs"),
        outputs=d.get("outputs"),
        extra=d.get("extra"),
        parent_run_id=d.get("parent_run_id"),
        parent_run_ids=d.get("parent_run_ids") or [],
        trace_id=d.get("trace_id"),
        start_time=_parse_iso(d.get("start_time")),
    )


def _walk_tree(root: dict):
    yield root
    for c in root.get("child_runs") or []:
        yield from _walk_tree(c)


def _fetch_s3_runs(s3_client: Any, uri: str) -> list[SimpleNamespace]:
    p = urlparse(uri)
    obj = s3_client.get_object(Bucket=p.netloc, Key=p.path.lstrip("/"))
    raw = gzip.decompress(obj["Body"].read())
    root = json.loads(raw)
    return [_run_dict_to_obj(r) for r in _walk_tree(root)]


def discover_threads_db_s3(
    conn: Any, projects: list[str], start_time: datetime, end_time: datetime
) -> list[tuple[str, str, list[str]]]:
    """Return (project, thread_id, [raw_data_uri, ...]) for every thread with at least one root
    run in [start, end). Per-thread URI list is unbounded in time so turns straddling the window
    boundary aren't lost — mirrors LangSmith's `metadata.thread_id` filter behavior.
    """
    sql = """
        WITH window_threads AS (
            SELECT DISTINCT thread_id
            FROM traces
            WHERE start_time >= %s AND start_time < %s
              AND project = ANY(%s)
              AND thread_id IS NOT NULL
        )
        SELECT t.project, t.thread_id, t.raw_data_uri
        FROM traces t
        JOIN window_threads wt USING (thread_id)
        WHERE t.raw_data_uri IS NOT NULL
        ORDER BY t.thread_id, t.start_time
    """
    by_thread: dict[tuple[str, str], list[str]] = {}
    with conn.cursor() as cur:
        cur.execute(sql, (start_time, end_time, projects))
        for proj, tid, uri in cur.fetchall():
            by_thread.setdefault((proj, tid), []).append(uri)
    return [(proj, tid, uris) for (proj, tid), uris in by_thread.items()]


def collect_events_for_thread_db_s3(
    s3_client: Any, project: str, thread_id: str, uris: list[str], lookup: PropertyTimezoneLookup
) -> list[dict]:
    """DB+S3 analog of `collect_events_for_thread`: download all root-run blobs, walk their
    `child_runs` trees, run the same extractor dispatch as the LangSmith path.
    """
    runs: list[Any] = []
    for u in uris:
        runs.extend(_fetch_s3_runs(s3_client, u))

    fallback_ctx, ctx_source = replay.resolve_thread_ctx(runs, thread_id)
    if ctx_source in ("missing", "no-interaction"):
        return []
    tools = [r for r in runs if r.run_type == "tool"]
    llm_runs = [r for r in runs if r.run_type == "llm"]
    root_runs = [r for r in runs if r.parent_run_id is None]
    voice_trace_ids = {str(r.trace_id) for r in tools if r.name == replay.THINKER_TOOL}
    prefetch_ids = {str(r.id) for r in runs if r.name == replay.PREFETCH_CHAIN_NAME}
    user_msg_by_trace = {str(r.id): (r.inputs or {}).get("message") if r.inputs else None for r in root_runs}

    events: list[dict] = []
    for run in tools:
        name = run.name
        inputs = run.inputs or {}
        outputs = run.outputs or {}
        if not inputs:
            continue
        if name in replay.HANDOFF_TOOLS:
            ev_list = replay.replay_handoff(name, inputs, fallback_ctx)
        elif name == replay.THINKER_TOOL:
            ev_list = replay.replay_thinker_qna(inputs, outputs, fallback_ctx)
        elif name == replay.CALL_TOOL:
            ancestors = {str(p) for p in (run.parent_run_ids or [])}
            if ancestors & prefetch_ids:
                continue
            ev_list = replay.replay_mcp_business_tool(inputs, outputs, fallback_ctx)
        elif name == replay.FACILITIES_THINKER_TOOL:
            ev_list = replay.replay_facilities_thinker(inputs, outputs, fallback_ctx)
        else:
            continue
        run_channel = replay.derive_run_channel(run, fallback_ctx)
        replay.rewrite_task_id(ev_list, thread_id, run_channel)
        replay.fill_property_timezone(ev_list, lookup)
        et = run.start_time.isoformat() if run.start_time else None
        for e in ev_list:
            events.append({"event_time": et, "event": e, "project": project, "thread_id": thread_id})

    seen_traces: set[str] = set()
    for llm in llm_runs:
        tid = str(llm.trace_id)
        if tid in voice_trace_ids or tid in seen_traces:
            continue
        parsed = replay.parse_responder_output(llm.outputs)
        if parsed is None:
            continue
        seen_traces.add(tid)
        ev_list = replay.replay_responder_output(parsed, user_msg_by_trace.get(tid), fallback_ctx)
        run_channel = replay.derive_run_channel(llm, fallback_ctx)
        replay.rewrite_task_id(ev_list, thread_id, run_channel)
        replay.fill_property_timezone(ev_list, lookup)
        et = llm.start_time.isoformat() if llm.start_time else None
        for e in ev_list:
            events.append({"event_time": et, "event": e, "project": project, "thread_id": thread_id})

    return events


def parse_event_time_to_ms(event_time: str | None) -> int | None:
    """Parse ISO event_time to Kafka millis. Treats naive timestamps as UTC."""
    if not event_time:
        return None
    text = event_time
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def event_dedup_key(record: dict) -> tuple[str, str, str | None]:
    ev = record["event"]
    return (ev["task"]["id"], ev["activity"]["summary"], record["event_time"])


class DedupLog:
    """Threadsafe append-only JSONL log of published-event keys.

    Pre-loads existing entries so re-runs skip already-published events
    before any produce call. Appended-to from the on_delivery callback,
    which fires from the producer's poll thread — so writes need a lock.
    Line-buffered + flushed on each write so a Ctrl-C only loses the
    in-flight callbacks, not buffered ones.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._seen: set[tuple[str, str, str | None]] = self._load()
        self._file = self.path.open("a", buffering=1)

    def _load(self) -> set[tuple[str, str, str | None]]:
        if not self.path.exists():
            return set()
        out: set[tuple[str, str, str | None]] = set()
        with self.path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    out.add((entry["task_id"], entry["activity_summary"], entry["event_time"]))
                except (json.JSONDecodeError, KeyError):
                    continue
        return out

    def __contains__(self, key: tuple[str, str, str | None]) -> bool:
        return key in self._seen

    def __len__(self) -> int:
        return len(self._seen)

    def record(self, key: tuple[str, str, str | None]) -> None:
        with self._lock:
            if key in self._seen:
                return
            self._seen.add(key)
            self._file.write(json.dumps({"task_id": key[0], "activity_summary": key[1], "event_time": key[2]}) + "\n")
            self._file.flush()

    def close(self) -> None:
        with self._lock:
            self._file.close()


def main() -> int:
    args = parse_args()

    if args.end_time <= args.start_time:
        print(
            f"ERROR: --end-time ({args.end_time.isoformat()}) must be strictly after "
            f"--start-time ({args.start_time.isoformat()}).",
            file=sys.stderr,
        )
        return 2

    load_env_file(args.env_file)
    if args.source == "langsmith" and not os.environ.get("LANGSMITH_API_KEY"):
        print("ERROR: LANGSMITH_API_KEY not set after loading env file.", file=sys.stderr)
        return 2
    if args.source == "db_s3" and not os.environ.get("EVAL_DB_URL"):
        print("ERROR: EVAL_DB_URL not set after loading env file (required for --source=db_s3).", file=sys.stderr)
        return 2

    topic = os.environ.get("KAFKA_TASK_ACTIVITY_TOPIC")
    if not topic:
        print(
            "ERROR: KAFKA_TASK_ACTIVITY_TOPIC not set after loading env file. Add it to your env file before running.",
            file=sys.stderr,
        )
        return 2
    if topic_looks_like_prod(topic) and not args.confirm_prod:
        print(
            f"REFUSING: KAFKA_TASK_ACTIVITY_TOPIC={topic!r} has no -qa/-sat suffix → looks like prod. "
            f"Pass --confirm-prod to override.",
            file=sys.stderr,
        )
        return 2

    source_projects = resolve_source_projects(args.source_projects)
    if not source_projects:
        print(
            "ERROR: --source-projects not given and env $RESIDENT_LANGSMITH_PROJECTS unset. "
            "Set the env var (comma-separated) or pass --source-projects explicitly.",
            file=sys.stderr,
        )
        return 2

    knock_token = os.environ.get("KNOCK_INTERNAL_AUTH_TOKEN")
    lookup = PropertyTimezoneLookup(knock_token)

    if args.source == "langsmith":
        from langsmith import Client

        client = Client()
        pairs: list[tuple[str, str, list[str]]] = [
            (p, t, []) for p, t in discover_threads(client, source_projects, args.start_time, args.end_time)
        ]
    else:  # db_s3
        # Suppress urllib3 InsecureRequestWarning emitted on every S3 GET when running through
        # a corporate TLS-inspection proxy. Cert chain rewriting trips urllib3 but the bytes
        # over the wire are still TLS-encrypted; nothing else fixes it cleanly here.
        import warnings

        import urllib3

        warnings.filterwarnings("ignore", category=urllib3.exceptions.InsecureRequestWarning)

        import boto3
        import psycopg

        s3 = boto3.Session(profile_name=args.aws_profile).client("s3")
        conn = psycopg.connect(os.environ["EVAL_DB_URL"], connect_timeout=10)
        pairs = discover_threads_db_s3(conn, source_projects, args.start_time, args.end_time)

    print(
        f"Source: {args.source}\n"
        f"Source projects ({len(source_projects)}): {', '.join(source_projects)}\n"
        f"Window: {args.start_time.isoformat()} → {args.end_time.isoformat()} (UTC, end-exclusive)\n"
        f"Topic: {topic!r}\n"
        f"Limit: {'unlimited' if args.limit is None else f'refuse-on-exceed at {args.limit}'}\n"
        f"Workers: {args.workers}",
        file=sys.stderr,
    )
    print(f"Discovered {len(pairs)} threads.", file=sys.stderr)

    def _collect(proj: str, tid: str, uris: list[str]) -> list[dict]:
        if args.source == "langsmith":
            return collect_events_for_thread(client, proj, tid, lookup)
        return collect_events_for_thread_db_s3(s3, proj, tid, uris, lookup)

    all_events: list[dict] = []
    completed = 0
    loop_start = time.monotonic()
    last_heartbeat = loop_start
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(_collect, proj, tid, uris): (proj, tid) for proj, tid, uris in pairs}
        for future in as_completed(futures):
            proj, tid = futures[future]
            completed += 1
            try:
                evs = future.result()
            except Exception as exc:
                print(f"  [{completed}/{len(pairs)}] ERROR thread {tid}: {exc!r}", file=sys.stderr)
                continue
            if evs:
                all_events.extend(evs)
                print(
                    f"  [{completed}/{len(pairs)}] {proj} thread {tid[:8]}... → {len(evs)} event(s)",
                    file=sys.stderr,
                )
            now = time.monotonic()
            if now - last_heartbeat >= HEARTBEAT_INTERVAL_S:
                elapsed = now - loop_start
                rate_per_hr = completed / elapsed * 3600 if elapsed > 0 else 0
                print(
                    f"  [heartbeat +{int(elapsed / 60)}m] "
                    f"{completed}/{len(pairs)} threads, "
                    f"{len(all_events)} events collected, "
                    f"~{rate_per_hr:.0f} threads/hr",
                    file=sys.stderr,
                    flush=True,
                )
                last_heartbeat = now

    if args.limit is not None and len(all_events) > args.limit:
        print(
            f"\nERROR: discovered {len(all_events)} events, exceeds --limit {args.limit}. "
            f"Re-run with a higher --limit or a narrower window. No events published.",
            file=sys.stderr,
        )
        return 2

    dedup = DedupLog(args.dedup_log)
    print(
        f"\nDedup log: {args.dedup_log} ({len(dedup)} pre-existing entries)",
        file=sys.stderr,
    )
    new_events = [e for e in all_events if event_dedup_key(e) not in dedup]
    skipped = len(all_events) - len(new_events)
    if skipped:
        print(f"Skipped {skipped} event(s) already in dedup log.", file=sys.stderr)

    print(f"\nCollected {len(new_events)} new event(s) to publish. Preview (first 10):", file=sys.stderr)
    for i, e in enumerate(new_events[:10]):
        ev = e["event"]
        ts_ms = parse_event_time_to_ms(e["event_time"])
        print(
            f"  #{i + 1}  event_time={e['event_time']}  ts_ms={ts_ms}  "
            f"channel={ev['extra']['channel']:5}  summary={ev['activity']['summary']!r}  "
            f"task.id={ev['task']['id']}",
            file=sys.stderr,
        )
    if len(new_events) > 10:
        print(f"  ... ({len(new_events) - 10} more)", file=sys.stderr)

    if args.dry_run:
        print(f"\nDRY-RUN: would publish {len(new_events)} event(s). Stopping.", file=sys.stderr)
        dedup.close()
        return 0

    if not new_events:
        print("No new events to publish.", file=sys.stderr)
        dedup.close()
        return 0

    from agent_leasing.kafka.task_activity.producer import build_task_activity_producer

    producer = build_task_activity_producer()
    if producer is None:
        print(
            "ERROR: producer not built. Check task_activity_event_publishing_enabled and kafka_task_activity_topic.",
            file=sys.stderr,
        )
        return 2
    if not producer.start():
        print(
            "ERROR: producer.start() returned False (cluster_not_configured or schema-registry failure).",
            file=sys.stderr,
        )
        return 2

    print(f"\nProducer started. Publishing to topic: {producer._topic}", file=sys.stderr)

    delivered: list[dict] = []
    failed: list[dict] = []

    def _make_callback(idx: int, et: str | None, dkey: tuple[str, str, str | None]):
        def _cb(err, msg):
            if err is not None:
                failed.append({"idx": idx, "event_time": et, "error": str(err)})
                print(f"  [#{idx + 1}] FAIL: {err}", file=sys.stderr)
            else:
                dedup.record(dkey)
                delivered.append(
                    {
                        "idx": idx,
                        "event_time": et,
                        "partition": msg.partition(),
                        "offset": msg.offset(),
                        "kafka_ts": msg.timestamp()[1],
                    }
                )

        return _cb

    for i, e in enumerate(new_events):
        event = e["event"]
        et = e["event_time"]
        ts_ms = parse_event_time_to_ms(et)
        key = (event.get("task") or {}).get("id") or "DEFAULT"
        dkey = event_dedup_key(e)
        try:
            producer._producer.produce(
                topic=producer._topic,
                key=key,
                value=event,
                timestamp=ts_ms or 0,
                on_delivery=_make_callback(i, et, dkey),
            )
            producer._producer.poll(0)
        except Exception as exc:
            failed.append({"idx": i, "event_time": et, "error": repr(exc)})
            print(f"  [#{i + 1}] EXC: {exc!r}", file=sys.stderr)

    print("\nFlushing producer...", file=sys.stderr)
    producer._producer.flush(30)
    producer.close()
    dedup.close()

    print(
        f"\nResult: {len(delivered)} delivered / {len(failed)} failed / {len(new_events)} attempted "
        f"(skipped {skipped} via dedup log)",
        file=sys.stderr,
    )
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
