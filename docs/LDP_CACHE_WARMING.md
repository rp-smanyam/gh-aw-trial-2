# LDP Cache Warming

Keeps LDP property data warm in Redis so resident flows do not pay cold-cache latency on the first request.

## Architecture

```
EventBridge (every 30 min)
  └─► Producer Lambda: <env>-ldp-cache-warmer
        ├─ Load secrets via the AWS Secrets Manager Lambda Extension
        ├─ GET AI_CONFIG_HOST/v3/properties/renter-ai-enabled → [property_ids]
        ├─ Chunk property_ids (default CHUNK_SIZE=100)
        └─ Send one SQS message per chunk
              └─► SQS queue: <env>-ldp-cache-warmer
                    ├─ Retry failed messages up to 3 times
                    ├─ Send poison messages to DLQ: <env>-ldp-cache-warmer-dlq
                    └─ Trigger Worker Lambda with batch_size=1
                          └─► Worker Lambda: <env>-ldp-cache-warmer-worker
                                ├─ Load secrets via the AWS Secrets Manager Lambda Extension
                                ├─ Initialize Redis connection inside the VPC
                                ├─ POST LDP_LOGIN_TOKEN_ENDPOINT → Bearer token
                                ├─ Warm the chunk with bounded async concurrency
                                └─ cache.set(
                                      "early:v2:ldp_property_data:{property_id}",
                                      [early_expire_at, parsed],
                                      expire=LDP_CACHE_TTL,
                                    )
```

## Runtime Behavior

- The producer runs every 30 minutes and only does external API fetch + chunking + SQS fan-out.
- The worker processes one SQS record per invocation. Each record contains one chunk of property IDs.
- Worker failures return `batchItemFailures`, so SQS retries only the failed record. This relies on `ReportBatchItemFailures` in the event source mapping.
- The worker retries transient LDP per-property failures once with linear backoff. Remaining per-property failures are logged but do not fail the SQS record, so the next 30-minute cycle retries naturally.

## Configuration

### Secrets Manager Keys

Both Lambdas read these values from the shared `agent-leasing` secret through the AWS Parameters and Secrets Lambda Extension:

| Key | Purpose |
|-----|---------|
| `AI_CONFIG_HOST` | AI Config API base URL |
| `AI_CONFIG_TOKEN` | Bearer token for the renter-AI-enabled property list endpoint |
| `LDP_RP_API_URL` | LDP API base URL |
| `LDP_LOGIN_TOKEN_ENDPOINT` | OAuth token endpoint |
| `LDP_LOGIN_CLIENT_ID` | OAuth client ID |
| `LDP_LOGIN_CLIENT_SECRET` | OAuth client secret |
| `LDP_CACHE_TTL` | Cache TTL written by the worker (default `2h`) |
| `LDP_CACHE_EARLY_TTL` | Early-refresh TTL stored alongside the cached value (default `1h30m`) |
| `CHUNK_SIZE` | Property IDs per SQS message (default `100`) |
| `WORKER_CONCURRENCY` | Max concurrent property warms per invocation (default `20`) |
| `LDP_WARM_MAX_RETRIES` | Retry count for transient `renter-read` failures (default `1`) |
| `LDP_WARM_RETRY_BACKOFF_SECONDS` | Base linear backoff between transient `renter-read` retries (default `1.0`) |

### Terraform-Managed Environment Variables

These are non-secret values set directly on the Lambda functions:

| Lambda | Var | Purpose |
|--------|-----|---------|
| Producer | `SECRETS_MANAGER_SECRET_ID` | Secret name used by the Lambda extension |
| Producer | `SQS_QUEUE_URL` | Queue URL for chunk fan-out |
| Worker | `SECRETS_MANAGER_SECRET_ID` | Secret name used by the Lambda extension |
| Worker | `REDIS_HOST` | ElastiCache endpoint |
| Worker | `REDIS_PORT` | Redis port (default `6379`) |

## Infra Notes

- Terraform lives in `infra/alpha-leasing/lambda.tf`, `infra/beta/lambda.tf`, and `infra/prod/lambda.tf`.
- Terraform bootstraps both Lambdas with a placeholder zip. CI/CD later updates both functions with the real package from `lambdas/ldp_cache_warmer/`.
- Both Lambdas run in the VPC. The producer was moved into the VPC because AI Config depends on VPC-reachable networking in practice.
- The worker runs in the VPC so it can reach Redis.
- Worker reserved concurrency is `5`, SQS visibility timeout is `180s`, and the worker timeout is `120s`.
- After the initial Terraform bootstrap, Lambda code deploys automatically:
  - alpha after a successful `CD Pipeline` run
  - beta after a successful `Deploy to Beta` run
  - prod after a successful `Deploy to Prod` run
- The GitHub deploy role in each environment must allow `lambda:UpdateFunctionCode`, `lambda:GetFunction`, and `lambda:GetFunctionConfiguration` on both cache warmer functions. Those permissions are added through `additional_github_deploy_permissions` in the environment Terraform module call.

## Bootstrap Runbook

The first deploy in each environment needs a one-time Terraform apply from the root infra module because the existing `task_definitions` deploy workflows only manage ECS task definitions, not the new Lambda resources.

### Alpha

1. `cd infra/alpha-leasing`
2. `terraform init`
3. `terraform apply -auto-approve`

### Beta

1. `cd infra/beta`
2. `terraform init`
3. `terraform apply -auto-approve`

### Prod

1. `cd infra/prod`
2. `terraform init`
3. `terraform apply -auto-approve`

After that bootstrap apply, `.github/workflows/lambda-deploy.yml` handles normal Lambda code deploys.

## Code Locations

- `lambdas/ldp_cache_warmer/handler.py` — `producer_handler`, `worker_handler`, shared helpers
- `lambdas/ldp_cache_warmer/requirements.txt` — Lambda dependencies
- `tests/lambdas/ldp_cache_warmer/test_handler.py` — Unit tests
- `.github/workflows/lambda-deploy.yml` — Lambda package + deploy workflow

## Fallback

`@cache.early` on `fetch_ldp_property_data()` in the service remains the fallback path. If the scheduled warmer misses a property or is temporarily down, the service fetches directly from LDP on cache miss and refreshes the cache in the background after `early_ttl`.

## Runtime Usage of Cached Data

The cached LDP response contains several fields consumed at request time:

| Field | Consumer | How it is surfaced |
|-------|----------|--------------------|
| `enabled_modules` | `get_disabled_modules_with_pte()` | Controls which features/tools are active |
| `pte_setting` | `get_disabled_modules_with_pte()` | Permission-to-enter default for maintenance |
| `resident_summary` | See below — depends on `property_marketing_info_tool_enabled` | Property marketing/descriptive information |

### `resident_summary` — Two Paths

The way `resident_summary` is surfaced is controlled by the `property_marketing_info_tool_enabled` setting (env var `PROPERTY_MARKETING_INFO_TOOL_ENABLED`):

**`True` (default — tool path):**
The `get_property_marketing_info` local function tool calls `fetch_ldp_property_data()` on demand when the model determines it needs property marketing or descriptive information (amenities, neighbourhood, positioning copy). Because the cache key `early:v2:ldp_property_data:{property_id}` is shared across all LDP consumers in a request, no additional LDP network call occurs. `context.property_data` is not populated.

**`False` (legacy — prompt injection path):**
During `__aenter__`, the agent calls `fetch_ldp_property_data()`, reads `resident_summary`, and stores it in `context.property_data`. The value is then injected directly into the system prompt via `{{ context.property_data }}` in `INSTRUCTIONS.md` and `VOICE_RESPONDER.md`. The `get_property_marketing_info` tool is not registered.

> The flag will be removed once the tool-based path is validated in production and the legacy path is no longer needed.
