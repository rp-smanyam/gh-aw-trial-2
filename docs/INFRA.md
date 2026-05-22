# Infrastructure & CI/CD Documentation

This document provides a comprehensive overview of the GitHub Actions workflows and Terraform infrastructure for the agent-leasing project.

---

## Table of Contents

- [Overview](#overview)
- [GitHub Workflows](#github-workflows)
  - [Workflow Relationships](#workflow-relationships)
  - [CI Pipeline](#ci-pipeline)
  - [CD Pipeline](#cd-pipeline)
  - [PR Validation](#pr-validation)
  - [Tests Workflow](#tests-workflow)
  - [Deploy to Beta](#deploy-to-beta)
  - [Deploy to Prod](#deploy-to-prod)
  - [Release Branch Helper](#release-branch-helper)
  - [External MCP Tests](#external-mcp-tests)
  - [Fortify AST Scan](#fortify-ast-scan)
- [Terraform Infrastructure](#terraform-infrastructure)
  - [Environment Structure](#environment-structure)
  - [ECS Services](#ecs-services)
  - [Task Definitions](#task-definitions)
  - [Supporting Infrastructure](#supporting-infrastructure)
- [AWS Accounts](#aws-accounts)
- [Image Tagging Strategy](#image-tagging-strategy)
- [Secrets Management](#secrets-management)

---

## Overview

The agent-leasing application uses a multi-environment deployment strategy with:
- **GitHub Actions** for CI/CD automation
- **AWS ECR** for Docker image storage
- **AWS ECS** for container orchestration
- **Terraform** for infrastructure as code

### High-Level Flow

```
Code Push → CI Pipeline (Tests + Build) → ECR (Image Storage) → CD Pipeline → ECS (Deployment)
```

---

## GitHub Workflows

### Workflow Relationships

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           WORKFLOW RELATIONSHIPS                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  PR Created/Updated                                                          │
│       │                                                                      │
│       ├──► pr-validation.yml (Branch naming, PR title, conflicts)            │
│       │                                                                      │
│       └──► ci.yml (Tests + Build - no push)                                  │
│                                                                              │
│  Push to alpha                                                               │
│       │                                                                      │
│       ├──► ci.yml (Tests + Build + Push to ECR with :alpha tag)              │
│       │         │                                                            │
│       │         └──► cd.yml (Auto-triggered, deploys to Alpha ECS)           │
│       │                                                                      │
│       └──► external-mcp-tests.yml (Tests against live MCP server)            │
│                                                                              │
│  Push to release/** branch                                                   │
│       │                                                                      │
│       ├──► ci.yml (Tests + Build + Push to ECR with :beta tag)               │
│       │                                                                      │
│       ├──► deploy-to-beta.yml (Manual trigger only)                          │
│       │                                                                      │
│       └──► Fortify-AST.yml (Security scan)                                   │
│                                                                              │
│  Manual Triggers                                                             │
│       │                                                                      │
│       ├──► tests.yml (Run tests on any branch)                               │
│       │                                                                      │
│       ├──► build-release-branch.yml (Create release from Jira tickets)       │
│       │                                                                      │
│       └──► Fortify-AST.yml (Security scan)                                   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### CI Pipeline

**File:** `.github/workflows/ci.yml`

The main continuous integration pipeline that runs tests, builds Docker images, and pushes them to ECR.

#### Triggers
- Push to `alpha` or `release/**` branches
- Pull requests to `alpha`
- Manual dispatch

#### Jobs

| Job | Description |
|-----|-------------|
| `tests-parallel` | Runs tests across 15 parallel shards with stubbed MCP and mock servers |
| `tests-serial` | Runs tests marked as `serial` (cannot be parallelized) |
| `coverage-report` | Combines coverage from all shards, enforces 85% minimum |
| `build` | Builds Docker images for main app and MCP service |
| `deploy-alpha` | Pushes images to ECR with `alpha` tags (on alpha branch push) |
| `deploy-release` | Pushes images to ECR with `beta` tags (on release branch push) |
| `deploy-production` | Pushes images to ECR with `stable`/`latest` tags (on main branch push) |

#### Test Infrastructure

Each test shard runs with:
- **Redis service container** for caching tests
- **Stubbed MCP server** (`MCP.dockerfile`) on port 8042
- **MockServer** for LDP API mocking on port 1080

#### Coverage Requirements
- Minimum threshold: **85%**
- Coverage data uploaded as artifacts and combined in `coverage-report` job

---

### CD Pipeline

**File:** `.github/workflows/cd.yml`

Deploys images from ECR to ECS. Automatically triggered after successful CI runs.

#### Triggers
- Automatically after CI Pipeline completes on `alpha`
- Manual dispatch with optional image tag

#### Deployment Process

1. **Get image tag** - Uses CI commit hash or manual input
2. **Configure AWS credentials** - OIDC authentication to deployment account
3. **Deploy Main Application**
   - Runs Terraform in `infra/alpha-leasing/task_definitions/`
   - Generates task definition JSON for both main and voice services
   - Registers main task definition with ECS
   - Updates main ECS service
4. **Deploy MCP Application**
   - Same process for `infra/alpha-mcp/task_definitions/`
5. **Deploy Voice Application**
   - Registers voice task definition from `infra/alpha-leasing/task_definitions/` (already generated by step 3)
   - Updates voice ECS service
6. **Wait for stabilization** - Waits up to 15 minutes for services to be healthy
7. **Verify deployment** - Confirms running count matches desired count for all three services

---

### PR Validation

**File:** `.github/workflows/pr-validation.yml`

Validates branch naming conventions and PR requirements.

#### Checks

| Check | Description |
|-------|-------------|
| Branch naming | Must match `KNCK-<number>` or `KNCK-<number>-<description>` |
| PR title | Must start with `KNCK-<number>` (or release format for PRs to main) |
| Source branch | PRs to `main` must come from `alpha` only |
| Branch freshness | Warns if >50 commits behind target branch |
| Merge conflicts | Fails if conflicts exist with target branch |
| Release notes | Required for PRs to `main` |

---

### Tests Workflow

**File:** `.github/workflows/tests.yml`

Standalone test runner for manual execution.

#### Triggers
- Manual dispatch only

#### Jobs
- `lint` - Ruff check and format validation
- `tests-parallel` - Same 15-shard parallel test execution as CI
- `coverage-report` - Combines and reports coverage

---

### Deploy to Beta

**File:** `.github/workflows/deploy-to-beta.yml`

Manual deployment to beta environment. Deploys both main and voice services.

#### Requirements
- Must be triggered from a `release/YYYY-MM-DD` branch
- Deploys to `knock_beta_rp_ai` cluster
- Uses AWS account `466903752538`

#### Deployment Process
1. Terraform apply in `infra/beta/task_definitions/` generates both `agent-leasing.json` and `agent-leasing-voice.json`
2. Registers main task definition and updates `beta-agent-leasing` service
3. Registers voice task definition and updates `beta-agent-leasing-voice` service
4. Waits for both services to stabilize (15-minute timeout)
5. Verifies running count matches desired count for both services

---

### Deploy to Prod

**File:** `.github/workflows/deploy-to-prod.yml`

Manual deployment to production environment. Deploys both main and voice services.

#### Requirements
- Must be triggered from a `release/YYYY-MM-DD` branch
- Deploys to `knock_prod_rp_ai` cluster
- Uses AWS account `481332135811`
- 20-minute stabilization timeout (vs 15 for other environments)

#### Deployment Process
1. Terraform apply in `infra/prod/task_definitions/` generates both `agent-leasing.json` and `agent-leasing-voice.json`
2. Registers main task definition and updates `prod-agent-leasing` service
3. Registers voice task definition and updates `prod-agent-leasing-voice` service
4. Waits for both services to stabilize (20-minute timeout)
5. Verifies running count matches desired count for both services

---

### Release Branch Helper

**File:** `.github/workflows/build-release-branch.yml`

Assists with creating release branches from Jira tickets.

#### Inputs
- `fix_version` - Jira fixVersion to pull tickets from
- `base_release` - Starting branch (format: `release/YYYY-MM-DD`)
- `target_release` - New branch name (format: `release/YYYY-MM-DD`)

#### Process
1. Fetches tickets from Jira matching the fixVersion
2. Generates a release matrix and cherry-pick commands
3. Uploads artifacts for manual review

---

### External MCP Tests

**File:** `.github/workflows/external-mcp-tests.yml`

Runs tests against the live alpha MCP server.

#### Triggers
- Push to `alpha` or `main`

#### Notes
- Uses `continue-on-error: true` - failures don't block CI
- Tests against `https://alpha-mcp-knock.knocktest.com/mcp/`
- Results are informational only

---

### Fortify AST Scan

**File:** `.github/workflows/Fortify-AST.yml`

Security scanning with Fortify on Demand.

#### Triggers
- Scheduled: 9th of every month at 3am
- Push to `release/**` branches
- PRs to `main`
- Manual dispatch

---

## Terraform Infrastructure

### Environment Structure

```
infra/
├── alpha-leasing/          # Alpha main application
│   ├── service.tf          # ECS service definition
│   ├── elasticache.tf      # Redis cluster
│   ├── terraform.tf        # Backend & providers
│   ├── terraform.tfvars    # Environment variables
│   ├── variables.tf        # Variable declarations
│   └── task_definitions/   # ECS task definitions (main + voice)
│       ├── agent-leasing.tf
│       ├── agent-leasing-voice.tf
│       └── secrets.tf
│
├── alpha-mcp/              # Alpha MCP service
│   ├── service.tf
│   ├── terraform.tf
│   ├── terraform.tfvars
│   ├── variables.tf
│   └── task_definitions/
│       ├── agent-leasing-mcp.tf
│       └── secrets.tf
│
├── beta/                   # Beta environment
│   ├── service.tf          # Main ECS service + voice deploy permissions
│   ├── voice.tf            # Voice ECS service
│   ├── elasticache.tf
│   ├── terraform.tf
│   ├── terraform.tfvars
│   ├── variables.tf
│   └── task_definitions/
│       ├── agent-leasing.tf
│       ├── agent-leasing-voice.tf
│       └── secrets.tf
│
└── prod/                   # Production environment
    ├── service.tf          # Main ECS service + voice deploy permissions
    ├── voice.tf            # Voice ECS service
    ├── elasticache.tf
    ├── terraform.tf
    ├── terraform.tfvars
    ├── variables.tf
    └── task_definitions/
        ├── agent-leasing.tf
        ├── agent-leasing-voice.tf
        └── secrets.tf
```

---

### ECS Services

Each environment deploys ECS services using the `tf-ecs-service` module.

#### Main Application Service (`agent-leasing`)

| Property | Alpha | Beta | Prod |
|----------|-------|------|------|
| Min instances | 2 | 2 | 5 |
| Max instances | 5 | 5 | 20 |
| Autoscaling metric | ALBRequestCountPerTarget | ALBRequestCountPerTarget | ALBRequestCountPerTarget |
| Target value | 30 | 30 | 30 |
| Internal service | No | No | No |
| Host header | `alpha-agent-leasing.knocktest.com` | `beta-agent-leasing.knocktest.com` | `agent-leasing.knockcrm.com` |

#### MCP Service (`agent-leasing-mcp`)

| Property | Alpha |
|----------|-------|
| Min instances | 1 |
| Max instances | 2 |
| Autoscaling metric | ECSServiceAverageMemoryUtilization |
| Target value | 66 |
| Internal service | Yes |
| Host header | `alpha-agent-leasing-mcp.knocktest.com` |

#### Voice Service (`agent-leasing-voice`)

Runs the same Docker image as the main service. Separated to enable independent scaling for voice workloads (real-time audio processing, WebSocket connections). ALB routing sends Twilio traffic to this service; the main service retains voice routes as a fallback.

| Property | Alpha                                | Beta | Prod |
|----------|--------------------------------------|------|------|
| Min instances | 1                                    | 2 | 10 |
| Max instances | 5                                    | 12 | 25 |
| Autoscaling metric | ECSServiceAverageCPUUtilization      | ECSServiceAverageCPUUtilization | `AWS/ECS CPUUtilization` (Maximum statistic, per-task) |
| Target value | 65                                   | 65 | 75 |
| Scale-in cooldown | 60s                                  | 60s | 300s |
| Internal service | No                                   | No | No |
| Host header | `alpha-agent-leasing-voice.knoThis ` | `beta-agent-leasing-voice.knocktest.com` | `agent-leasing-voice.knockcrm.com` |
| Deregistration delay | 120s                                 | 120s | 600s |

Note: All voice services use CPU-based scaling tuned for real-time audio processing. Prod scales on the Maximum statistic of per-task CPU rather than the fleet average, so the autoscaler reacts when any single task approaches saturation — a better fit for sticky WebSocket calls that bin-pack onto individual tasks. Alpha and beta still use the fleet-average predefined metric. Deregistration delay allows active calls to drain before instances are removed; prod uses 600s to fit longer call durations, alpha and beta use 120s.

---

### Task Definitions

Task definitions are generated via Terraform and define the container configuration.

#### Main Application Task

Each task contains two containers:

1. **nginx-proxy** (sidecar)
   - Image: `nginx-proxy:proxy-pass-websocket`
   - Memory: 256MB
   - CPU: 64 units
   - Port: 8080 (external)
   - Proxies to application container

2. **agent-leasing** (application)
   - Image: `agent-leasing:{tag}` from ECR
   - Memory: 8192MB
   - CPU: 1024 units
   - Port: 8000 (internal)
   - Secrets injected from AWS Secrets Manager

#### Voice Service Task

Same image as the main application. No mock-server container. Defined in each environment's `task_definitions/agent-leasing-voice.tf` alongside the main task definition to share a single `secrets.tf`.

1. **nginx-proxy** (sidecar)
   - Image: `nginx-proxy:proxy-pass-websocket`
   - Memory: 256MB
   - CPU: 64 units
   - Port: 8080 (external)

2. **agent-leasing** (application)
   - Image: `agent-leasing:{tag}` from ECR (same image as main)
   - Memory: 8192MB
   - CPU: 1024 units (same as main)
   - Port: 8000 (internal)

3. **otel-collector** (sidecar)
   - CPU: 128 units
   - Memory: 2048MB

#### MCP Service Task

Similar structure with:
- **nginx-proxy** on port 8080
- **agent-leasing-mcp** on port 8042

---

### Supporting Infrastructure

#### ElastiCache (Redis)

Each environment has a Redis cluster for caching:

| Property | Value |
|----------|-------|
| Engine | Redis 7.1 |
| Node type | cache.t3.micro |
| Nodes | 1 |
| Snapshot retention | 5 days |

#### IAM Roles

Created by the `tf-ecs-service` module:
- **Task Role**: Allows Secrets Manager access
- **Execution Role**: Allows SSM parameter and Secrets Manager access
- **GitHub Upload Role**: For CI to push images to ECR
- **GitHub Deploy Role**: For CD to update ECS services

---

## AWS Accounts

| Account | ID | Purpose |
|---------|-----|---------|
| Knock-Shared-Services | 171267611104 | ECR repository hosting |
| RenterAI-Product (Alpha) | 969504524223 | Alpha ECS deployment |
| CC-Non-Prod (Beta) | 466903752538 | Beta ECS deployment |
| Production | 481332135811 | Production ECS deployment |
| Shared-Networking | 688951274555 | DNS management |

---

## Image Tagging Strategy

Images are stored in ECR at `171267611104.dkr.ecr.us-east-1.amazonaws.com/agent-leasing`

| Tag Pattern | When Created | Purpose |
|-------------|--------------|---------|
| `main-{7-char-sha}` | Every CI build | Commit-specific main app image |
| `mcp-{7-char-sha}` | Every CI build | Commit-specific MCP image |
| `alpha` | Push to alpha branch | Latest alpha main app |
| `mcp-alpha` | Push to alpha branch | Latest alpha MCP |
| `beta` | Push to release branch | Latest beta main app |
| `mcp-beta` | Push to release branch | Latest beta MCP |
| `latest` | Push to main branch | Latest production main app |
| `stable` | Push to main branch | Stable production main app |
| `mcp-latest` | Push to main branch | Latest production MCP |
| `mcp-stable` | Push to main branch | Stable production MCP |

---

## Secrets Management

Secrets are stored in AWS Secrets Manager and injected into containers at runtime.

### Secret Categories

| Category | Examples |
|----------|----------|
| API Keys | `OPENAI_API_KEY`, `LANGSMITH_API_KEY`, `PRISMA_AIRS_API_KEY` |
| MCP Auth | `KNOCK_MCP_AUTH_CLIENT_SECRET`, `FACILITIES_MCP_AUTH_CLIENT_SECRET` |
| Kafka | `KAFKA_REPORTING_DATA_API_KEY`, `KAFKA_REPORTING_DATA_API_SECRET` |
| Twilio | `KNOCK_TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` |
| Model Config | `MODEL`, `MODEL_REASONING_EFFORT`, per-thinker model settings |
| Guardrails | `ENABLED_INPUT_GUARDRAILS`, `ENABLED_OUTPUT_GUARDRAILS` |

### Secret ARN Format
```
arn:aws:secretsmanager:us-east-1:{account}:secret:agent-leasing-{hash}:{key}::
```

---

## Quick Reference

### Trigger a Manual Deployment

```bash
# Deploy to alpha
gh workflow run cd.yml --ref alpha

# Deploy specific image to alpha
gh workflow run cd.yml --ref alpha -f image_tag=main-abc1234

# Deploy to beta (from release branch)
gh workflow run deploy-to-beta.yml --ref release/2025-01-08

# Deploy to prod (from release branch)
gh workflow run deploy-to-prod.yml --ref release/2025-01-08
```

### Check Deployment Status

```bash
# List recent workflow runs
gh run list --workflow=cd.yml

# Watch a specific run
gh run watch <run-id>

# View failed logs
gh run view <run-id> --log-failed
```

---

Return to the main [README](../README.md) or see [DEPLOYMENT.md](DEPLOYMENT.md) for deployment procedures.
