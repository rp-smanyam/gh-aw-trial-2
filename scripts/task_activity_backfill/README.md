# Resident `task_activity_event` backfill (DB + S3)

Replay past resident agent traces → publish equivalent
`task_activity_event` Kafka messages, so the downstream daily-brief SP
sees historical conversation activity. Uses the live extractor functions
(`agent_leasing.kafka.task_activity.extractors.*`) directly, so backfill
output stays bit-for-bit consistent with what live publishes today.

**Source:** the team's internal `alpha_agentic_evals` Postgres `traces`
table is the discovery index. Each `traces.raw_data_uri` points to a
gzipped LangSmith Run JSON on `s3://alpha-agentic-evals/...`, which is
the actual payload we replay. DB query is index-supported (`<1 s` for
thousands of pairs); S3 GETs parallelize freely (no rate limit).

## Prerequisites

`uv sync` for `confluent-kafka` (already in pyproject). When running the
publisher with `--source db_s3` (default), additional deps are loaded
ad-hoc via `uv run --with 'psycopg[binary]' --with boto3 ...`.

The scripts have **no built-in environment defaults** — every run picks
up its config from the env file you pass via `--env-file`.

Required env vars (in your env file):

- `EVAL_DB_URL` — Postgres URI for `alpha_agentic_evals` (e.g.
  `postgresql://USER:PASS@HOST:5432/alpha_agentic_evals?sslmode=require`).
- `KAFKA_TASK_ACTIVITY_TOPIC` — the destination topic. Names ending in
  `-qa` / `-sat` are non-prod and run unconditionally; anything else is
  treated as prod and refuses to publish without `--confirm-prod`.
- `KAFKA_REPORTING_DATA_BOOTSTRAP_SERVERS` + matching API key/secret
- `KAFKA_REPORTING_DATA_SCHEMA_REGISTRY_URL` + matching API key/secret
- `KNOCK_INTERNAL_API_URL` — host for the property-timezone lookup.
- `RESIDENT_LANGSMITH_PROJECTS` — comma-separated list of project names
  to filter on. Same values as live's project name (e.g.
  `alpha_renter_ai_resident_chat`); the publisher uses these as the
  `project` column filter on the DB.

Optional:

- `KNOCK_INTERNAL_AUTH_TOKEN` — bearer token for
  `GET /v1/admin/property/<id>`. Without it, voice/chat events that
  didn't carry `property_timezone` from upstream stay unset and the
  brief SP falls back to UTC for those.

AWS access (S3 GETs):

- Configure an SSO profile in `~/.aws/config` for the account that owns
  the `alpha-agentic-evals` bucket. Default profile name: `alpha`
  (override with `--aws-profile`).
- Refresh before each session: `aws sso login --profile alpha`. SSO
  tokens are short-lived; an expired session shows up as every thread
  failing with `UnauthorizedSSOTokenError`.

## Coverage limits

- **Windows ≥ 2026-04-14.** S3 archival started 4/14. Earlier rows
  exist in the DB but have NULL `raw_data_uri` → no payload to replay.

## End-to-end workflow

1. **Inspect one S3 blob** (optional sanity check on data shape):
   ```bash
   # Find a raw_data_uri for a thread
   psql "$EVAL_DB_URL" -c "SELECT raw_data_uri FROM traces \
       WHERE thread_id = '<uuid>' AND raw_data_uri IS NOT NULL LIMIT 1;"

   # Download + inspect
   aws --profile alpha s3 cp <s3-uri> - | gunzip | jq '.child_runs[].name'
   ```
   Each blob is one root LangSmith Run with full `child_runs` tree.

2. **Preview a window** (no Kafka write):
   ```bash
   uv run --with 'psycopg[binary]' --with boto3 \
       scripts/task_activity_backfill/publish_backfill_events.py \
       --env-file <env-file> \
       --start-time 2026-04-14 --end-time 2026-04-15 \
       --source db_s3 --workers 16 --dry-run
   ```
   `--start-time` / `--end-time` are UTC; bounds are start-inclusive,
   end-exclusive. ISO 8601 (`2026-04-14T00:00:00Z`) and date-only
   (`2026-04-14` = midnight UTC) are both accepted.

3. **Publish** — drop `--dry-run`. The publisher writes a `(task.id,
   activity.summary, event_time)` triple to `--dedup-log` (default
   `data/backfill_volume/published.jsonl`) per successful delivery, so
   re-runs of the same window skip already-published events.

   ```bash
   uv run --with 'psycopg[binary]' --with boto3 \
       scripts/task_activity_backfill/publish_backfill_events.py \
       --env-file <env-file> \
       --start-time 2026-04-14 --end-time 2026-04-15 \
       --source db_s3 --workers 16
   ```

   Add `--limit <N>` as a refuse-on-exceed safety guard for wide
   windows. Targeting a topic without `-qa`/`-sat` suffix also requires
   `--confirm-prod`.

4. **Verify on Kafka / BigQuery** — confirm `kafka_ts == event_time`
   (not publish-time) and payload fields look right. Landing table:
   `ai-data-ingestion-staging.lz_rei_agent_topic.task_activity_event`;
   resident MV:
   `ai-data-ingestion-staging.dm_resident_ai.resident_ai_activity_event_mv`.

## Files

| File | Role |
|---|---|
| `publish_backfill_events.py` | Kafka publisher with `--source {langsmith,db_s3}`. DB+S3 discovery + S3 GET + extractor dispatch + dedup-log + Kafka publish. |
| `replay_trace_activity_events.py` | Extractor module. Used as a library by the publisher (`replay.replay_*`, `replay.resolve_thread_ctx`). |
| `property_lookup.py` | `PropertyTimezoneLookup` — Knock admin-API helper with in-process cache. |
| `env_loader.py` | Minimal dotenv loader shared across scripts. |

## Key design notes

- **Thread + channel is the unit.** `metadata.thread_id` is the
  conversation grouping but a single thread can span multiple channels
  (Redis cache keys on `chat_session_id` alone, so SMS and EMAIL turns
  for the same resident share one `thread_id`). Backfill `task.id` is
  `uuid5(NS, "<channel>:<thread_id>")` — channel partitions mirror
  live's `kafka/task_id.build_task_id`, so cross-channel turns get
  distinct task.ids instead of collapsing. Each event's `extra.channel`
  is also stamped from the originating run's product, not the
  thread-level borrowed/synthesized ctx. The thread/(chat_session_id+
  session_marker) divergence remains — backfill window closed before
  live publishing began, so the surfaces never collide on a conversation.

- **Per-thread S3 fetch is unbounded in time.** Discovery filters root
  runs by `start_time IN [start, end)`. The S3 fetch then pulls *all*
  root runs for those threads regardless of time — so turns that
  straddle the window boundary (one turn at 12:59, the next at 13:01)
  aren't lost. Mirrors LangSmith's `metadata.thread_id` filter
  behaviour. Verified on the 5/8 morning window: 19 threads, 11 events,
  bit-identical to the LangSmith path's output.

- **`event_time` is set explicitly when publishing.** Without it the
  Kafka broker stamps publish-time on every backfill message and
  downstream collapses everything into the publish day. The publisher
  passes `event_time` as the message timestamp via
  `producer.produce(..., timestamp=ts_ms)`.

- **ctx fallback chain:** `borrow → synthesize → skip ("no-interaction")`.
  Most threads borrow a real `SessionScope` from a sibling tool run that
  serialized `RunContextWrapper`. Voice qna-only threads (no transfer)
  fall through to a synthesized minimal ctx. Threads with no business
  metadata at all (voice short-circuit traces) are skipped — live
  wouldn't have emitted events either.

- **Internal prefetch is filtered out.** `prefetch_property_overview_and_insights`
  fires three MCP calls with `skip_post_processors=True`; live bypasses
  the activity emitter for them. The flag isn't in trace inputs, so we
  detect via ancestor chain run name and skip those `call_tool` runs.

## Performance

Measured at `--workers=16` on alpha resident:

| Window | Threads | Events | Wall clock | Per-thread |
|---|---:|---:|---:|---:|
| 5/8 8-9 ET (1 hour) | 19 | 11 | ~6 s | ~310 ms |
| 4/24 18:30-18:50 (20 min, peak) | 58 | 42 | ~11 s | ~180 ms |
| 4/24 full day | 912 | 944 | ~45 s | ~50 ms |
| 4/14 full day (first day of S3 archival, live publish) | 2,536 | 925 | ~5 min | ~120 ms |
| 4/15 full day (dry-run, after urllib3 warning suppression) | 2,543 | 381 skipped via dedup | **34 s** | **~13 ms** |
| 4/16 full day (live publish, post-suppression) | 2,605 | — | **32 s** | **~12 ms** |

Steady-state per-thread settled at **~12-13 ms** after suppressing
urllib3 `InsecureRequestWarning` chatter.

LangSmith API path at `--workers=5` runs ~10× slower on the same
windows and hits 429 rate limits at higher worker counts.

## Known limits

- **No frustrated-user dedup.** Live emits FRUSTRATED_USER once per
  session via a delivery-time callback (`extract_frustrated_user_events`
  + `on_success`). Replay doesn't reproduce that callback, so a session
  where the model flips `user_frustrated=True` on multiple turns will
  yield one event per turn. Downstream may need to dedup if this matters.
- **Active-handoff short-circuit not wired.** `_handle_active_handoff`
  in server.py emits a `Handoff to Staff - Already in Handoff` event
  without a tool call, and short-circuits before `metadata.thread_id` is
  set. Thread-based filtering doesn't see these traces. Frequency in a
  100-SMS sample was 0/100 so impact is negligible.

## Safety rails on the publisher

- `--start-time` / `--end-time` are UTC and required. Bounds are
  `[start, end)` — start inclusive, end exclusive.
- `KAFKA_TASK_ACTIVITY_TOPIC` must be set — missing → hard ERROR before
  any Kafka call.
- Topic without `-qa`/`-sat` suffix → refuses unless `--confirm-prod`.
- `--limit <N>` is a refuse-on-exceed safety guard. When set, the
  publisher exits non-zero before any Kafka call if discovery turns up
  more than N events. Default: unlimited.
- `--dry-run` collects + previews events without publishing.
- `--dedup-log` records every successfully-published event triple. Re-runs
  of the same window skip already-published events automatically. Default:
  `data/backfill_volume/published.jsonl`.
- Every successful delivery logs partition / offset / `kafka_ts`. The
  per-event `task.id` is also printed so you can cross-reference against
  the source thread.
