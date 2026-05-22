# Testing

A comprehensive set of unit, integration, and end-to-end tests are included in the `tests` directory. 

## Running Tests

### Running Unit Tests

```shell
uv run pytest tests/unit
```

To run all tests including LLM-as-judge (matches CI pipeline):
```shell
uv run pytest tests --cov --cov-report term --cov-fail-under=85 -n 6
```

### Running Specific Test Types

The full test suite can take some time, so it is recommended to run only the tests you need.  

To run just a single directory:

```shell
uv run pytest tests/integration/agent/resident/resident_one/
```

To run specific agent file:
```shell
uv run pytest tests/integration/agent/resident/resident_one/test_resident_agent.py
```

To run a specific test:
```shell
uv run pytest tests/integration/agent/resident/resident_one/test_resident_agent.py::test_response_correctness[facilities_service_request_1]
```

### Running Tests in GitHub

Integrations tests in tests/integration are resource intensive so it's easier to run these remotely in GitHub.

- Install [gh](https://cli.github.com/)
- Log in to GitHub with `gh auth login`
- Run `gh workflow run .github/workflows/tests.yml` against the default branch
- Run `gh workflow run .github/workflows/tests.yml --ref my-branch-name` against another branch
- Find job logs: `gh run list --workflow="tests.yml"`
- For job # `20442028287`:
  - View job: `gh run view 20442028287`
  - View logs: `gh run view 20442028287 --log`
  - Tail logs: `gh run watch 20442028287`
  - View as JSON: `gh run view 20442028287 --json jobs,status,conclusion`
  - View failures: `gh run view 20442028287 --log-failed`

# Test Details

## Test Cases

Test cases are defined in a single file to create a single source of truth across all agent types and modalities.  This is to keep the business logic constant, minimize code duplication, and make it easier to maintain.  The test cases are defined in as a pytest parameterize array in `tests/integration/agent/resident/_test_cases.py`.

This means that if you are creating a new test case, you should not need to modify any testing code and only create a new entry in the array.  Creating a new Agent will require creating a new folder and test file(s), but the test cases will be the same.

Note, these are primarily for testing semantic equivalence of the agents.  Testing additional specific behaviors may require custom test code.

## Pool-Based Threshold Testing

Guardrail and LLM-as-judge tests are non-deterministic because they run against live LLMs. Instead of retrying individual failures with `@pytest.mark.flaky`, these tests use **pool-based threshold testing**: all parametrized cases are pooled together and the pool passes if failures stay within allowed limits. No retries.

### Marker API

```python
@pytest.mark.pool(threshold=0.9)                      # auto-named pool (scoped to this function)
@pytest.mark.pool(threshold=0.9, name="legal_advice")  # named pool (shared across functions)
@pytest.mark.pool(threshold=0.9, min_failures=1)       # tolerate at least 1 failure (default)
```

- `threshold` (float): minimum pass rate (0.0–1.0). Default 0.9.
- `name` (str, optional): pool name. When provided, multiple test functions share one pool for combined scoring.
- `min_failures` (int, optional): minimum number of failures always tolerated, regardless of threshold. Default 1. This prevents small pools (e.g., 3 tests) from failing on a single flaky result.

### How it works

The pool plugin (`tests/pool_plugin.py`) intercepts test results via pytest hooks. Individual failures are converted to `xfail` so they don't fail the session immediately. After all tests run, each pool is evaluated: it passes when `failures <= max(allowed_by_threshold, min_failures)`. If any pool exceeds its allowed failures, the session fails.

A pool summary is printed at the end of every run:

```
================================= pool results =================================
legal_advice: 14/14 passed (100%), 0 failed <= 1 allowed  PASSED
response_correctness_sms: 103/110 passed (94%), 7 failed <= 10 allowed  PASSED
```

When failures occur, a warning banner is printed in a separate "pool failure details" section prompting developers to investigate before merging.

### xdist compatibility

The plugin assigns `xdist_group` markers so all tests in the same pool run on one worker under `-n auto`. Pool members are also kept on the same CI shard by `conftest.py`'s pool-to-shard assignment. Results are forwarded to the controller via report sections.

### Adding new non-deterministic tests

When adding guardrail or LLM-as-judge tests that exercise live LLMs:

1. Add `@pytest.mark.pool(threshold=0.9)` (or with a shared `name=` if multiple functions test the same guardrail)
2. Do **not** use `@pytest.mark.flaky` — that masks regressions by silently retrying

## LLM-as-Judge Evaluations

LLM-as-judge tests are non-deterministic and marked with `@pytest.mark.llm_judge`. They run in the CI pipeline using pool-based threshold testing (see above) and are also included in the manually-triggered Tests workflow (`tests.yml`).

To run only LLM-as-judge tests:
```shell
uv run pytest tests -m llm_judge
```

The testing framework includes sophisticated LLM-as-judge evaluations for semantic equivalence and correctness:

### Semantic Equivalence Testing
Tests whether agent responses are semantically equivalent to expected outputs, even if the exact wording differs:

```python
# Example from test suite
await assert_semantic_equivalence(
    aclient, semantic_equivalence_judge, ask_request,
    input="What are the office hours?",
    expected_output="Office hours are Monday-Friday 9am-6pm",
    expected_score=0.4
)
```

### Multi-Turn Conversation Testing
Tests agent behavior across multiple conversation turns:

```python
# Example multi-turn test
input_output_pairs = [
    ("Hello", "Hi! How can I help you today?"),
    ("What's my rent?", "Your rent is $1,200 per month"),
    ("When is it due?", "Rent is due on the 1st of each month")
]
await assert_semantic_equivalence_diff_multi_turn_pairs(
    semantic_equivalence_judge, agent, context, input_output_pairs
)
```

## Test Configuration

### Stubbed MCP Server
Tests use a stubbed MCP server (`tests/stubbed_mcp.py`) that provides consistent, predictable responses for all MCP tools without requiring external dependencies.

### Environment Setup
Test configuration is handled in `tests/conftest.py`:
- Disables Kafka reporting and telemetry during tests
- Forces use of stubbed MCP servers
- Provides fixtures for different agent types and contexts
- Sets up LLM judges for evaluation

## Writing New Tests

When adding new agent functionality:

1. **Add Unit Tests**: Test individual functions and components
2. **Add Integration Tests**: Test agent interactions with MCP tools
3. **Add LLM Judge Tests**: Validate semantic correctness of responses
4. **Update Stubbed MCP**: Add any new tool responses needed for testing
5. **Document Test Cases**: Include test cases in `tests/integration/agent/resident/_test_cases.py`

For detailed testing guidelines and examples, see the existing test files in each agent's test directory.

## Load Testing

Load tests use [Locust](https://locust.io/) and live in `tests/load/locustfile.py`. They target both the HTTP chat endpoint (`/v1/agent/ask`) and the Twilio voice WebSocket (`/media-stream/websocket`).

### Running locally

```shell
# Web UI (interactive tuning) — opens http://localhost:8089:
uv run locust -f tests/load/locustfile.py

# Headless quick smoke test (5 users, 1 user/sec, 30 seconds):
uv run locust -f tests/load/locustfile.py --headless -u 5 -r 1 -t 30s

# Chat-only or voice-only:
uv run locust -f tests/load/locustfile.py --headless -u 20 -r 5 -t 2m AgentAskUser
uv run locust -f tests/load/locustfile.py --headless -u 10 -r 2 -t 2m TwilioVoiceUser
```

### Running against remote environments

Use the `scripts/run_locust.sh` wrapper, which works around an SSL incompatibility between `pip_system_certs` and gevent:

```shell
LOAD_TEST_PAYLOAD=data/payloads/beta-chat.json ./scripts/run_locust.sh \
    --host https://beta-agent-leasing.knocktest.com --headless -u 10 -r 2 -t 2m AgentAskUser
```

### Fetching real payloads

`scripts/fetch_payload.py` pulls a recent payload from CloudWatch logs for use with load tests:

```shell
uv run scripts/fetch_payload.py alpha voice 141
uv run scripts/fetch_payload.py beta chat 141 --property-id 21521
```

Saved payloads go to `data/payloads/` and can be passed via the `LOAD_TEST_PAYLOAD` or `LOAD_TEST_VOICE_PAYLOAD` environment variables.

### User types

| User class | Endpoint | Behavior |
|------------|----------|----------|
| `AgentAskUser` | `/v1/agent/ask` | Multi-turn conversation (same session ID) |
| `SingleTurnUser` | `/v1/agent/ask` | Fresh session per request (cold-start testing) |
| `TwilioVoiceUser` | `/media-stream/websocket` | Simulates a Twilio call with μ-law silence frames |

### Key environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LOCUST_HOST` | `http://localhost:8000` | Target host |
| `LOAD_TEST_PAYLOAD` | SMS example payload | JSON file for chat/SMS/email requests |
| `LOAD_TEST_VOICE_PAYLOAD` | Voice example payload | JSON file for voice requests |
| `VOICE_CALL_DURATION` | `30` | Simulated call length in seconds |

## Mocking HTTP endpoints

HTTP endpoints can be mocked with mock server. When you run docker compose it will use an expectations 
file at `tests/mockserver-expectations.json`. Adjust that file to change the behavior of the mock server, 
which runs on port `1080`.

Environment variables can be changed to use the mock server. For example:

```
LDP_RP_API_URL=http://localhost:1080
LDP_AUTH_ENABLED=True
LDP_LOGIN_TOKEN_ENDPOINT=http://localhost:1080/login/identity/connect/token
LDP_LOGIN_CLIENT_ID="1"
LDP_LOGIN_CLIENT_SECRET="1"
```

## Calling the streaming endpoint

The `streaming_client.py` script will call the streaming endpoint.

```shell
 uv run src/examples/streaming/streaming_client.py
```
To test streaming with a chat UI clone [uc-chat-service](https://tfs.realpage.com/tfs/Realpage/Consumer%20Solutions/_git/uc-chat-service)
and follow the instructions. You will want to point the server to `http://localhost:8000/v1/agent/stream`.

Also see [Streaming](STREAMING.md).
