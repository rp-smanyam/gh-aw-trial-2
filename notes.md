# Perf Improver Notes — rp-smanyam/gh-aw-trial-2

## Repository Snapshot (first run, 2026-05-22)

This is a **trial repository** with essentially no application code. Total content:

- `README.md` — single line: `# gh-aw trial`
- `.gitattributes` — marks `.lock.yml` files as generated
- `.vscode/settings.json` — enables Copilot for markdown
- `.github/mcp.json` — wires up the gh-aw MCP server
- `.github/agents/agentic-workflows.agent.md` — dispatcher agent doc
- `.github/workflows/copilot-setup-steps.yml` — Copilot setup (checkout + install gh-aw CLI)
- `.github/workflows/daily-perf-improver.md` + `.lock.yml` — this workflow

## Build/Test/Benchmark Commands

**None discovered.** There is no package.json, Makefile, go.mod, Cargo.toml, pyproject.toml, or build script of any kind. The repository has no compilable or testable code.

The only relevant command in CI is the gh-aw CLI install in `copilot-setup-steps.yml`:
- `gh aw mcp-server` (via MCP)
- `gh-aw-actions/setup-cli@v0.74.8` to install the CLI

If/when source code is added, candidate commands to check:
- npm: `npm ci`, `npm test`, `npm run build`, `npm run lint`
- python: `pip install -e .`, `pytest`, `ruff check`, `mypy`
- go: `go build ./...`, `go test ./...`, `go test -bench`
- rust: `cargo build`, `cargo test`, `cargo bench`

## Performance Opportunities Backlog

**Empty.** No code exists to optimize. The workflow YAML/markdown files are config, not hot paths.

Possible future areas to monitor (only if code is added):
- (none yet)

## Performance Notes / Techniques

- N/A — no code present.

## Repository Conventions

- No `AGENTS.md` present.
- `.lock.yml` files are generated from sibling `.md` workflow files — never edit `.lock.yml` directly; regenerate with `gh aw compile`.

## Open PRs / Issues

As of 2026-05-22 11:25 UTC:

- PR #1: `Add daily-perf-improver agentic workflow` — **merged** 2026-05-22T11:21:52Z. Not a perf-improver PR.
- No other PRs.
- No open issues. The first run's `create_issue` for the monthly activity summary evidently did not materialize (no issue with that title exists). Re-queued in run 26284971633; verify on next run.
- No `performance` label exists in the repo.

## Cross-run lessons

- **Verify safe-output side effects on next run.** Memory marked the monthly activity issue as "Created in this run; number filled in after issue is created downstream" — but the issue never appeared. Always re-check repo state at the start of a run against what memory claims, and treat unset `number` fields as "unverified, possibly missing".
