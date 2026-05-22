# AI Coding Assistant Guide

This is the authoritative guide for AI coding assistants working on this project. Follow these instructions to assist developers effectively.

---

## Quick Reference

| Task                                                | Command |
|-----------------------------------------------------|---------|
| Start backend services without server               | `docker compose up` |
| Start server (assumes backend services are running) | `uv run server` |
| Run unit tests                                      | `uv run pytest tests/unit` |
| Run all tests with coverage                         | `uv run pytest tests --cov --cov-report term --cov-fail-under=85 -n 6` |
| Check formatting                                    | `uv run ruff check` |
| Fix formatting                                      | `uv run ruff check --fix && uv run ruff format` |
| Add dependency                                      | `uv add <package>` |
| Sync dependencies                                   | `uv sync` |

---

## Related Repositories

| Service | Description | Repository |
|---------|-------------|------------|
| GenAI service | GenAI service | https://github.com/knockrentals/cai-genai-service |
| Facilities resident AI | Facilities resident AI | https://github.com/RealPage/facilities-resident-ai |
| Knock MCP | Knock MCP server | https://github.com/RealPage/mcp-knock |
| Loft MCP | Loft MCP server | https://github.com/RealPage/mcp-loft |
| OneSite MCP | OneSite MCP server | https://github.com/RealPage/mcp-onesite |
| Facilities MCP & API | Facilities MCP server and API | https://dev.azure.com/Realpage-Azure/SpendAndAccounting/_git/facilities-service-request-mcp-server |

---

## External Systems & Tooling

Use the tools below proactively when investigating problems or understanding context.

### LangSmith

When a developer provides any trace-related ID (LangSmith run ID, `call_sid`, `chat_session_id`, `openai_trace_id`, `thread_id`), use LangSmith to look it up right away.

> **⚠ CRITICAL: You MUST read the `LANGSMITH_API_KEY` from the project's `.env` file before making any LangSmith API calls.** Do not assume it is already configured or available in the environment.

> **⚠ CRITICAL: You MUST specify the correct project name when searching for runs.** A trace ID will return no results if you search the wrong project. Projects follow the pattern `<env>_renter_ai_resident_<channel>`. Examples: `prod_renter_ai_resident_voice`, `prod_renter_ai_resident_sms`, `alpha_renter_ai_resident_chat`. If the channel is unknown, **try both voice and non-voice projects** or use `list_projects` to discover them. Do not give up after searching a single project.

LangSmith MCP is typically pre-configured in your coding assistant's MCP settings. Use the MCP tools directly to fetch and search traces.

### GitHub CLI (`gh`)

Use `gh` for all GitHub operations: reading PRs, issues, CI runs, and code history. This is essential for understanding context around a bug or feature. If not yet authenticated, run `gh auth login` first.

Active work is tracked in GitHub issues and on org-level project boards at https://github.com/orgs/RealPage/projects.

```shell
# View a PR
gh pr view <number>

# View CI run logs
gh run view <run-id> --log-failed

# List recent runs for a workflow
gh run list --workflow="tests.yml"

# Trigger a test run on a branch
gh workflow run .github/workflows/tests.yml --ref <branch>

# Search issues
gh issue list --search "<query>"

# View file history / blame context
gh api repos/:owner/:repo/commits?path=<file>
```

### AWS CLI

Use the `aws` CLI to access ECS, CloudWatch logs, or Secrets Manager.

> **⚠ CRITICAL: EVERY `aws` command MUST include `--profile <env>` (e.g., `--profile prod`, `--profile beta`).** Commands without `--profile` will silently use the wrong account and return empty or misleading results. If you don't know the environment, ask for it.

> **⚠ CRITICAL: When searching CloudWatch logs, use the correct log group for the channel.** Voice traffic logs to `/ecs/<env>-agent-leasing-voice`; all other channels (chat, SMS, email) log to `/ecs/<env>-agent-leasing`. If the channel is unknown, search BOTH log groups.

Profiles must exist in `~/.aws/config`; run `aws sso login` if credentials are expired.

```shell
# Re-authenticate (run when credentials are expired)
aws sso login

# Tail live logs (use -voice log group for voice traffic)
aws logs tail /ecs/prod-agent-leasing --follow --profile prod
aws logs tail /ecs/prod-agent-leasing-voice --follow --profile prod

# Query logs with filter (e.g., find a specific call_sid)
# Voice channel → -voice log group; all other channels → main log group
aws logs filter-log-events \
  --log-group-name /ecs/prod-agent-leasing \
  --filter-pattern '"<call_sid_or_trace_id>"' \
  --profile prod
aws logs filter-log-events \
  --log-group-name /ecs/prod-agent-leasing-voice \
  --filter-pattern '"<call_sid_or_trace_id>"' \
  --profile prod

# Fetch secrets
aws secretsmanager get-secret-value --profile <profile> \
  --secret-id agent-leasing \
  --query 'SecretString' --output text 2>/dev/null \
  | jq -r 'to_entries | sort_by(.key) | .[] | "\(.key)=\(.value)"'
```

---

## Troubleshooting & Observability

When a developer provides an ID for troubleshooting, use it to pull correlated data from LangSmith (via MCP) and AWS CloudWatch. These systems share common IDs, making cross-system correlation straightforward.

### ID Lookup Reference

Use this table to determine exactly how to look up each ID type in each system.

| ID | Also known as | LangSmith (via MCP) | AWS CloudWatch | Notes |
|----|---------------|---------------------|----------------|-------|
| `langsmith_trace_id` | LangSmith trace ID | **Get run by run ID directly** | Use metadata from the run (see below) | Metadata contains `environment`, `channel`, and AWS-searchable IDs like `chat_session_id`, `call_sid`, `openai_trace_id` |
| `openai_trace_id` | OpenAI trace ID | Search runs where metadata `openai_trace_id` = value | Filter logs: `'"<value>"'` | Use LangSmith/AWS; no programmatic OpenAI lookup available |
| `call_sid` | Call ID, Twilio call SID | Search runs where metadata `call_sid` = value | Filter logs: `'"<value>"'` | Voice channel only |
| `chat_session_id` | Session ID, chat session | Search runs where metadata `chat_session_id` = value | Filter logs: `'"<value>"'` | |
| `thread_id` | Thread ID | Search runs where metadata `thread_id` = value | Filter logs: `'"<value>"'` | |

### Troubleshooting Workflow

To troubleshoot effectively you need two things: (1) a description of the problem and (2) a LangSmith trace ID. Everything else (environment, channel, correlated IDs) can be derived from LangSmith metadata. **Before searching any logs or making API calls, make sure you have both.** If you don't, ask the developer up front rather than guessing or wasting time on blind searches.

Where to get them:
- **GitHub issue provided** — fetch it first with `gh issue view <number>`. The description and LangSmith trace ID/URL are often in the issue body or comments.
- **No issue provided** — check the current git branch name, which may contain an issue number. If found, fetch that issue with `gh issue view <number>` for context.
- If after checking all available sources you are still missing the description or the trace ID, ask the developer for what's missing before proceeding.

Once you have both, work the problem as follows:

1. **Understand the bug** — clarify what should have happened vs. what actually happened.
2. **Read `.env`** to get `LANGSMITH_API_KEY`. Do this before any LangSmith calls.
3. **Fetch the traces** from LangSmith via MCP immediately. For each provided ID:
   - `langsmith_trace_id`: get the run directly by run ID (no project name needed for direct lookups). Then **extract metadata fields** from the run: `environment` (e.g., `prod`, `beta`, `alpha`), `channel` (e.g., `voice`, `chat`, `sms`), and any AWS-searchable IDs (`chat_session_id`, `call_sid`, `openai_trace_id`). These metadata fields tell you everything you need to search AWS — do not ask the developer for the environment or channel.
   - Any other ID: search runs by metadata key/value pair (see ID Lookup Reference table). **Use the correct project name** (`<env>_renter_ai_resident_<channel>`). If the environment or channel is unknown, search multiple projects (e.g., both `_voice` and `_chat`/`_sms`).
4. **Search AWS logs** using the environment and channel derived from LangSmith metadata (step 3) or provided by the developer. **Pick the correct log group based on channel:** voice → `/ecs/<env>-agent-leasing-voice`, everything else → `/ecs/<env>-agent-leasing`. If the channel is unknown, search both. Use an AWS-searchable ID from the metadata (e.g., `chat_session_id`, `call_sid`, `openai_trace_id`) as the filter pattern.
   ```shell
   # Non-voice (chat, SMS, email)
   aws logs filter-log-events \
     --log-group-name /ecs/<env>-agent-leasing \
     --filter-pattern '"<the-id-value>"' \
     --profile <env>

   # Voice
   aws logs filter-log-events \
     --log-group-name /ecs/<env>-agent-leasing-voice \
     --filter-pattern '"<the-id-value>"' \
     --profile <env>
   ```
5. **Read the relevant code** using the Key Files table.
6. **Check GitHub** for recent changes to those files, or review the referenced PR (`gh pr view <number>`, `git log -- <file>`).
7. **Synthesize findings** — correlate timestamps, error messages, and tool calls across all sources.

### CloudWatch Log Groups

Log group names follow the pattern `/<env>-<service>`. Replace `<env>` with `alpha`, `beta`, or `prod`.

| Service | Alpha | Beta | Prod |
|---------|-------|------|------|
| Agent Leasing | `/ecs/alpha-agent-leasing` | `/ecs/beta-agent-leasing` | `/ecs/prod-agent-leasing` |
| Agent Leasing Voice | `/ecs/alpha-agent-leasing-voice` | `/ecs/beta-agent-leasing-voice` | `/ecs/prod-agent-leasing-voice` |
| Knock MCP | `/ecs/alpha-mcp-knock` | `/ecs/beta-mcp-knock` | `/ecs/prod-mcp-knock` |
| Loft MCP | `/ecs/alpha-mcp-loft` | `/ecs/beta-mcp-loft` | `/ecs/prod-mcp-loft` |

### Structured Log Fields

Every agent-leasing log line includes these fields — use any of them as CloudWatch filter patterns:

```
channel=<channel>  request_id=<id>  openai_trace_id=<id>  chat_session_id=<id>
property_id=<id>  prospect_id=<id>
```

Example log line (JSON as emitted in AWS):
```json
{
  "event": "Input items: [{'role': 'user', 'content': 'hello'}]",
  "logger": "agent-leasing",
  "level": "info",
  "timestamp": "2025-06-25T11:00:00.000000Z",
  "channel": "renter_ai_prospect_chat",
  "chat_session_id": "1",
  "openai_trace_id": "2",
  "property_id": "3",
  "prospect_id": "4",
  "request_id": "5"
}
```

---

## Documentation References

Read relevant docs before making changes. The docs folder is the authoritative source for design decisions and architectural patterns.

| Topic                | Document                                               |
|----------------------|--------------------------------------------------------|
| **Architecture & design** | `docs/DESIGN.md` — agent organization, request flows, SessionScope, MCP, module system, prompt injection |
| Development workflow | `docs/DEVELOPMENT.md`                                  |
| Testing guide        | `docs/TESTING.md`                                      |
| Voice interaction    | `docs/VOICE_INTERACTION.md` — legacy `twilio_handler.py` implementation |
| Voice architecture   | `docs/VOICE_ARCHITECTURE.md` — refactored voice package (`src/agent_leasing/voice/`) |
| Streaming            | `docs/STREAMING.md`                                    |
| Guardrails           | `docs/GUARDRAILS.md`                                   |
| Logging              | `docs/LOGGING.md`                                      |
| Infrastructure       | `docs/INFRA.md`                                        |
| Deployment           | `docs/DEPLOYMENT.md`                                   |
| API endpoints        | `docs/ENDPOINTS.md`                                    |
| Language switching   | `docs/LANGUAGE_SWITCHING.md`                           |
| MCP optimization     | `docs/MCP_OPTIMIZATION.md`                             |
| LDP cache warming    | `docs/LDP_CACHE_WARMING.md`                            |
| SMS consent          | `docs/SMS_CONSENT.md`                                  |
| Verification         | `docs/VERIFICATION.md`                                 |
| Voice filler phrases | `docs/FILLER_PHRASES.md`                               |
| Resident agent       | `src/agent_leasing/agent/resident_one_agent/README.md` |
| Architecture decisions | `docs/adr/`                                          |

---

## Critical Rules

### Testing

**Always run tests after making changes.**

- Add unit tests for new functions
- Run **unit tests relevant to the change** after any code change — run the full unit suite only if explicitly asked or the change is broad enough to warrant it
- **Do NOT run integration or e2e tests locally unless explicitly asked** — they require backend services; prefer triggering them in GitHub CI instead (see [Running Tests in GitHub CI](#running-tests-in-github-ci))
- If integration or e2e tests must be run locally, both require the stubbed MCP server (`docker build -f MCP.dockerfile . -t stubbed-mcp && docker run -d -p 8042:8042 stubbed-mcp`) and backend services (`docker compose up`)
- **LLM-as-judge tests run automatically in GitHub CI** using pool-based threshold testing (see [LLM-as-Judge Testing](#llm-as-judge-testing))
- Try to fix flaky or retried tests, but don't get stuck on them

### Documentation

**Keep `docs/` in sync with code changes.**

After any code change, check whether it affects documented behavior and update the relevant file(s) in `docs/`. A doc update is required when the change:

- Alters how agents, tools, or MCP servers are configured or behave
- Adds, removes, or renames a product, agent, module, or guardrail
- Changes a request/response flow, API endpoint, or session lifecycle
- Modifies a pattern that is explicitly described in `docs/DESIGN.md` or another doc

Use the [Documentation References](#documentation-references) table to find which doc covers the area you changed.

### Git Workflow

**The `alpha` branch is the default branch and it is protected. All changes require pull requests.**

- Always branch from `alpha` (not `main`)
- Before requesting review, complete the PR template checklist (`.github/pull_request_template.md`).

### Package Management

**Use `uv` exclusively. Never use `pip`, `pip-tools`, or `poetry` directly.**

### Code Quality

**Run formatting before committing:**
```shell
uv run ruff check --fix && uv run ruff format
```

### Readability (Reviewer-First)

**Optimize for code review readability.**

- Prefer clear, explicit code over cleverness or "code golf"
- Optimize for diff readability (low cognitive load, minimal mental bookkeeping)
- Use small, well-named helpers and early returns instead of deep nesting
- Avoid dense comprehensions, tricky one-liners, or surprising data structures unless they materially simplify the code

---

## Project Overview

This is a leasing agent implementation built on the OpenAI Agents SDK.
The application runs as a FastAPI server providing AI agents for different
personas (prospects, applicants, residents) across multiple communication
channels (chat, SMS, email, voice). Agents interact with external tools through
MCP (Model Context Protocol) servers.

### Key Technologies
- **Server**: FastAPI
- **Agent Framework**: OpenAI Agents SDK
- **External Tools**: MCP (Model Context Protocol)

For a detailed walkthrough of the architecture — agent organization, request flows, `SessionScope`, `CachingMCPServer`, the module system, and prompt injection — read `docs/DESIGN.md`.

---

## Key Files and Locations

| Category              | Location                                           |
|-----------------------|----------------------------------------------------|
| Agent implementations | `src/agent_leasing/agent/`                         |
| API endpoints         | `src/agent_leasing/server.py`                      |
| Agent utilities       | `src/agent_leasing/agent/util.py`                  |
| Settings              | `src/agent_leasing/settings.py` (uses `.env` file) |
| Guardrails            | `src/agent_leasing/agent/guardrails/`              |
| Local Tools           | `src/agent_leasing/agent/tools/`                   |
| Thinkers (Legacy)     | `src/agent_leasing/agent/thinkers/`                |
| Test configuration    | `tests/conftest.py`                                |
| Unit tests            | `tests/unit/`                                      |
| Integration tests     | `tests/integration/`                               |
| E2E tests             | `tests/e2e/`                                       |

---

## Testing

### Test Organization

| Type | Marker | Location | Requirements | When to Run |
|------|--------|----------|--------------|-------------|
| **Unit tests** | — | `tests/unit/` | None | Run tests relevant to the change; full suite only when explicitly asked or the change is significant |
| **Integration tests** | — | `tests/integration/` | Stubbed MCP server + backend services | Only when explicitly asked (locally), or via GitHub CI |
| **E2E tests** | — | `tests/e2e/` | Stubbed MCP server + backend services | Only when explicitly asked |
| **LLM-as-judge tests** | `@pytest.mark.llm_judge` | `tests/integration/` | Stubbed MCP server + backend services | Manually before a PR — **excluded from automatic CI builds** |

### Test Requirements

- **Framework**: pytest
- **Coverage target**: 85% minimum
- **Async testing**: Use `pytest-asyncio`
- **Naming**: Files with `test_` prefix or `_test` suffix
- **Pattern**: Follow Arrange-Act-Assert
- **Fixtures**: Use existing fixtures; avoid creating new mocks when possible
- **Isolation**: Keep tests isolated and independent

### Common Testing Commands

```shell
# Run unit tests relevant to your change (prefer this over running the full suite)
uv run pytest tests/unit/test_example.py

# Run the full unit suite (only when explicitly asked or the change is significant)
uv run pytest tests/unit

# Run specific test
uv run pytest tests/unit/test_example.py::test_function_name

# Run with coverage report
uv run pytest --cov --cov-report html
# View at coverage/index.html

# Run tests in parallel (use with caution - can cause subtle issues)
uv run pytest -n 5

# Run LangSmith-annotated tests only
uv run pytest -m langsmith
```

### Running Tests in GitHub CI

Prefer running integration tests in GitHub CI rather than locally:

```shell
# Trigger test workflow
gh workflow run .github/workflows/tests.yml --ref BRANCH_NAME

# List recent runs
gh run list --workflow="tests.yml"

# View specific run (replace with actual run ID)
gh run view RUN_ID

# Watch logs in real-time
gh run watch RUN_ID

# View only failures
gh run view RUN_ID --log-failed
```

### LLM-as-Judge Testing

> **These tests run automatically in GitHub CI using pool-based threshold testing.** Non-deterministic failures are tolerated within pool thresholds (see `tests/pool_plugin.py`). If pool failures appear in CI, investigate whether your changes caused them before merging.

Tests use LLM-based semantic evaluation:
- `assert_semantic_equivalence()`: Single-turn semantic matching
- `assert_semantic_equivalence_diff_multi_turn_pairs()`: Multi-turn conversation testing
- Requires `LANGSMITH_API_KEY` in `.env` for tracing

To run locally:
```shell
uv run pytest tests -m llm_judge
```

Then monitor the run:

```shell
gh run list --workflow="tests.yml"
gh run watch <run-id>
gh run view <run-id> --log-failed
```

### Fixed Test Time

Tests inject a fixed date/time of **June 25, 2025, at 11am** for consistency.

---

## Prompt Requirements

Prompts are Jinja2 markdown templates located in agent folders (e.g., `INSTRUCTIONS.md`, `VOICE_RESPONDER.md`).

### Format
- File extension: `.md`
- Template syntax: `{{ variable }}` (Jinja2)
- `SessionScope` is always available in templates
- Versioning supported: `INSTRUCTIONS_V2.md`, `INSTRUCTIONS_V3.md`, etc.

### Model Optimization

| Prompt file | Target model |
|-------------|--------------|
| `INSTRUCTIONS.md` | `gpt-5.4` |
| `VOICE_RESPONDER.md` | `gpt-realtime-2` |

**Before editing `INSTRUCTIONS.md`**, read the [GPT-5.4 Prompt Guidance](https://developers.openai.com/api/docs/guides/prompt-guidance) and apply its guidance. Key principles from that guide take precedence over general prompt writing advice.

**Before editing `VOICE_RESPONDER.md`**, consult the [Realtime 2.0 Prompting Guide](https://developers.openai.com/api/docs/guides/realtime-models-prompting) for model-specific constraints (e.g., no structured output, latency sensitivity, turn-taking behavior).

### Quality Standards

Prompts must be:
- **Concise** — avoid unnecessary verbosity
- **Grammatically correct** — free of spelling errors
- **Consistent** — use uniform formatting throughout
- **Non-redundant** — don't repeat instructions
- **Non-conflicting** — avoid contradictory instructions
- **Jargon-free** — no technical or business jargon
- **Well-organized** — logical structure and flow
- **Self-contained** — understandable independent of implementation

Reference: [OpenAI Best Practices](https://help.openai.com/en/articles/6654000-best-practices-for-prompt-engineering-with-the-openai-api)

---

## Python Guidelines

### Code Style
- Follow PEP 8 conventions
- Use type hints for all function parameters and return values
- Prefer f-strings over `.format()` or `%` formatting
- Use meaningful variable and function names

### Function Design
- Keep functions small and focused (single responsibility)
- Document complex functions with docstrings
- Prefer returning early over deep nesting

### Imports
- Use absolute imports over relative imports

### Error Handling
- Use specific exception types; avoid bare `except` clauses
- Log errors with appropriate context

---

## Local Development URLs

| Service | URL |
|---------|-----|
| Chatbot | http://localhost:8000/chatbot |
| OpenAPI docs | http://localhost:8000/docs |
| Voice UI | http://localhost:8000/voice-ui |
| Mock server dashboard | http://localhost:1080/mockserver/dashboard |
| MCP Inspector | http://localhost:6274/ |

### MCP Inspector

To inspect which tools are exposed by the local MCP server:

```shell
npx @modelcontextprotocol/inspector
# Set transport to "Streamable HTTP" with URL http://localhost:8042/
```
