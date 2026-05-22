# Perf Improver Notes — rp-smanyam/gh-aw-trial-2

## Repository Snapshot (updated 2026-05-22 run 26285308600)

This repo received a major source code drop between runs. It now contains the
**agent-leasing** application: a FastAPI + OpenAI Agents SDK service that
provides AI agents across chat, SMS, email, and voice channels. Key directories:

- `src/agent_leasing/` — ~168 Python files; FastAPI server, agents, tools, voice
- `tests/` — ~216 Python test files (unit/integration/e2e/llm_judge)
- `scripts/` — utility scripts (load testing via locust, log analysis, etc.)
- `infra/` — alpha/beta/prod infrastructure
- `docs/` — extensive design docs (DESIGN.md, INFRA.md, etc.)
- `AGENTS.md` — canonical AI assistant guide (commands, conventions, troubleshooting)

## Build/Test/Benchmark Commands

From `AGENTS.md` (validated where possible):

- **Build deps**: `uv sync`
- **Unit tests**: `uv run pytest tests/unit`
- **Full tests w/ coverage**: `uv run pytest tests --cov --cov-report term --cov-fail-under=85 -n 6`
- **Lint**: `uv run ruff check`
- **Format**: `uv run ruff check --fix && uv run ruff format`
- **Load test**: `scripts/run_locust.sh`
- **Coverage target**: 85% minimum

### Environment quirks for the agent runner

- Project requires `python>=3.13.1,<3.14`; the agent runner ships Python 3.14.5.
- `uv sync` will fail because of the version constraint.
- Workaround for validation: standalone `ruff` works fine via `pip install --user ruff` and runs against the source directly. Use `ruff format --check <file>` to verify a single file without rewriting.
- Cannot run the full test suite locally — rely on CI for that.

## Performance Opportunities Backlog

See `state.json` `perf_opportunities_backlog`. Top open candidates after run 26285308600:

1. ✅ **DONE in run 26285308600**: pre-compile regexes in `verify_resident_identity.py`
2. `voice_text_normalizer.py:_NORMALIZERS` — strip lambda wrappers
3. `helpers.py:resolve_greeting_placeholders` — three `.replace()` calls
4. `mcp_post_processors.py:add_currency` — two `.replace()` per Decimal
5. `input_sanitizers.py:sanitize_urls` — `str.replace` loop on growing string
6. `otel_configuration.py:110` — two `.replace()` to strip scheme (low priority)

## Performance Notes / Techniques

- **Pre-compiled regex pattern**: `verify_resident_identity_v2.py` already uses `_UNIT_PREFIX_RE = re.compile(...)`. The v1 file did not until run 26285308600. When extending compiled regex use, mirror the v2 convention (`_UPPER_SNAKE_RE`).
- **Benchmark methodology that worked**:
  1. Dynamically load the modified module via `importlib.util.spec_from_file_location` (stubs out missing deps with a dummy module).
  2. Copy the pre-change function verbatim as a reference.
  3. `re.purge()` between trials to nullify Python's regex cache.
  4. 3 trials × 20K iterations × representative inputs. Speedups <2% variance across trials.
- **Parity verification** is essential for "no-behavior-change" claims. Run the reference and modified implementations on a hand-built corpus that covers each branch + edge cases. Used 60 cases in run 26285308600.

## Repository Conventions

- **Branch off `alpha`, not `main`** (per AGENTS.md). `main` is the GitHub default branch shown to git tooling, but the actual project default is `alpha`. PRs from `perf-assist/*` branches should target `alpha`.
- **`.lock.yml` files are generated**; never edit directly.
- **No `pip`, `poetry`, or `pip-tools`** — use `uv` exclusively.
- **Ruff line length**: 119.
- **Lint excludes** include `fortify.py`, `scripts/`, `aspire-app/`, `src/examples/`.
- **`tests/*` has relaxed ruff** (`D` and `UP` ignored).

## Open PRs / Issues (as of 2026-05-22 11:35 UTC)

- **PR (perf-improver)**: `[perf-improver] perf: pre-compile regex patterns in verify_resident_identity` — created run 26285308600, draft, pending CI. Branch `perf-assist/precompile-verify-resident-regexes`. ~2.6x / 2.2x speedup.
- **Issue (perf-improver)**: `[perf-improver] Monthly Activity 2026-05` — created run 26285308600 with labels `performance` + `automation`. Verify number on next run.
- No other open issues or PRs.
- `performance` label: created automatically when the monthly issue was created (was missing in earlier runs).

## Cross-run lessons

- **The repo can change drastically between runs.** Prior memory said "no source code"; one commit later it has 168+ Python files. Always re-snapshot at the start of each run and don't trust stale assumptions.
- **`create_issue` from prior runs can silently no-op.** Two attempts (runs 26284286573, 26284971633) failed to create the monthly activity issue. Always re-verify on the next run; never assume the side effect succeeded.
- **Python version mismatch blocks `uv sync`** in the agent runner — work around with standalone tools (ruff via `pip install --user`) and Python-stdlib-only benchmarks.
- **v1 ↔ v2 dispatch pattern**: a `settings.use_candidate_generation_verifier` flag picks between two implementations of the same tool. When optimizing one side, check whether the other already solved the problem (v2 had it solved).
- **The default branch for PRs is `alpha`** per AGENTS.md, even though git tooling shows `main`. Note this when reviewing whether a PR is targeting the right base.
