# Resident Identity Verification

This document describes the resident identity verification flow for VOICE, SMS, and EMAIL channels.

## Overview

The verification system ensures residents confirm their identity (unit number and optionally birth year) before accessing sensitive operations like creating service requests, issuing parking passes, or viewing rent information.

**Key properties:**
- CHAT channel is exempt (users are pre-authenticated via portal login)
- Verification can be globally disabled via `IDENTITY_VERIFICATION_ENABLED=false` (all channels behave like CHAT)
- Verification status persists for the session duration (tracked via boolean flags)
- The tool never reveals expected values to the user
- Verification is enforced by MCP pre-processors (primary) and prompt instructions (secondary for local tools)
- Once verified, the session remembers the verification status so re-verification is not required

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                           Request Flow                                    │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  User Request ──► LLM reads INSTRUCTIONS.md                              │
│                        │                                                  │
│                        ▼                                                  │
│              ┌─────────────────────┐                                     │
│              │ verify_resident_    │  Returns:                           │
│              │ identity tool       │  - verified (bool)                  │
│              │                     │  - mismatched_fields (list)         │
│              └─────────────────────┘                                     │
│                        │                                                  │
│                        │  Sets identity_verified=true on SessionScope    │
│                        ▼                                                  │
│              ┌─────────────────────┐                                     │
│              │ Protected Tool      │  MCP tools have pre-processor that  │
│              │ (local or MCP)      │  checks identity_verified flag      │
│              └─────────────────────┘                                     │
│                        │                                                  │
│          ┌────────────┴────────────┐                                     │
│          ▼                         ▼                                      │
│   [verified=true]          [verified=false]                              │
│   Tool executes            VerificationError raised                      │
│                                                                           │
└──────────────────────────────────────────────────────────────────────────┘
```

## Protected Tools

| Tool | Type | Verification Required |
|------|------|----------------------|
| `call_facilities_thinker_via_api` | Local | Unit number |
| `create_service_request` | MCP (Facilities) | Unit number |
| `get_active_service_requests` | MCP (Facilities) | Unit number |
| `issue_guest_parking_pass` | MCP (Loft) | Unit number |
| `get_rent_information` | MCP (OneSite) | Unit number + birth year |

## Components

### 1. Session State

**File:** `src/agent_leasing/models/context.py`

Verification status is tracked on `SessionScope` via boolean flags:

```python
class SessionScope:
    # Identity verification status (set by verify_resident_identity tool)
    # Tracks whether the resident has been verified this session
    identity_verified: bool = False
    identity_verified_with_birth_year: bool = False
```

### 2. Verification Tool

**File:** `src/agent_leasing/agent/tools/verify_resident_identity.py`

The `verify_resident_identity` tool compares user-provided values against stored product info:

```python
@function_tool(description_override=DESCRIPTION)
async def verify_resident_identity(
    ctx: RunContextWrapper[Any],
    unit_number: Annotated[str, "The unit number provided by the resident"],
    birth_year: Annotated[str | None, "Birth year (YYYY). Required for rent/balance tools."] = None,
) -> dict:
    # Returns: {"verified": bool, "mismatched_fields": [...]}
```

**Behavior:**
- Normalizes unit numbers (case-insensitive, removes spaces and common prefixes like "unit", "apt", "#")
- If the stored unit is bare numeric and the caller provides the same numeric core plus one trailing letter (for example, `"19"` vs `"19C"`), verification treats them as equivalent
- Extracts birth year from date formats like `MM/DD/YYYY` or `YYYY-MM-DD`
- Updates `identity_verified` and `identity_verified_with_birth_year` flags on success
- Never reveals expected values in response
- Returns `MISSING_DATA` action and `verified=false` immediately if required verification data (`ab_unit_number` or `date_of_birth`) is missing from the payload — there is nothing to compare against, so the agent should transfer to staff

**Normalization examples:**
- `"12 A"` → `"12A"`
- `"Unit 204"` → `"204"`
- `"apt. 5B"` → `"5B"`
- Cross-channel comparison fallback: stored `"19"` matches caller input `"19C"`

### 3. Agent Registration

**File:** `src/agent_leasing/agent/resident_one_agent/agent.py`

The verification tool is registered conditionally:

```python
# Add verification tool for non-CHAT channels
if channel != "CHAT":
    local_tools.append(verify_resident_identity)
```

### 4. MCP Pre-Processor Enforcement

**Files:**
- `src/agent_leasing/agent/tools/verification_check.py`
- `src/agent_leasing/agent/tools/mcp_pre_processors.py`

MCP tools have a pre-processor that checks verification status before the tool is called:

```python
# verification_check.py
PROTECTED_TOOLS = {
    "call_facilities_thinker_via_api": False,  # Unit only
    "create_service_request": False,           # Unit only
    "get_active_service_requests": False,      # Unit only
    "issue_guest_parking_pass": False,         # Unit only
    "get_rent_information": True,              # Unit AND birth year
}

def check_verification_status(context: SessionScope, tool_name: str) -> tuple[bool, str | None]:
    """Check if verification requirements are met for a tool."""
    if get_channel_from_context(context) == "CHAT":
        return True, None  # Chat users are pre-authenticated

    requires_birth_year = PROTECTED_TOOLS[tool_name]

    if not context.identity_verified:
        return False, "VERIFICATION_REQUIRED: Call verify_resident_identity first."

    if requires_birth_year and not context.identity_verified_with_birth_year:
        return False, "VERIFICATION_REQUIRED: Call verify_resident_identity with birth_year first."

    return True, None
```

The pre-processor is attached to MCP tools in `agent.py`:

```python
# For non-CHAT channels, add verification pre-processor
if channel != "CHAT" and "issue_guest_parking_pass" in enabled_tools:
    pre_processor_extras["issue_guest_parking_pass"] = [
        verification_pre_processor("issue_guest_parking_pass")
    ]
```

If verification is missing, the pre-processor raises `VerificationError` and the tool call fails.

### 5. Prompt Instructions

**File:** `src/agent_leasing/agent/resident_one_agent/INSTRUCTIONS.md`

The prompt renders verification status and context-appropriate guidance using Jinja2 conditionals. It does not list protected tools (the pre-processor handles enforcement). Instead, it shows the current verification state and tells the agent what to do next:

- **Not verified**: Ask for unit number (and birth year for rent tools), call `verify_resident_identity`, handle success/failure
- **SMS/EMAIL, unit verified but not birth year**: Tell the agent to ask for birth year before rent/balance requests
- **Fully verified**: Tell the agent not to re-verify

## Channel-Specific Behavior

### CHAT Channel
- No verification section in prompt
- No `verify_resident_identity` tool available
- Protected tools are not gated by verification

### VOICE Channel
- Verification section included in prompt
- `verify_resident_identity` tool available
- Agent must verify unit number before using protected tools
- Birth year is additionally required for `get_rent_information`

### SMS and EMAIL Channels
- Verification section included in prompt
- `verify_resident_identity` tool available
- Agent must verify unit number before using protected tools
- Birth year is additionally required for `get_rent_information`

## Flow Examples

### Successful Verification (SMS)

```
User: "I need to report a leak in my kitchen"
Agent: "For security, could you confirm your unit number?"
User: "Unit 64"
Agent: [calls verify_resident_identity(unit_number="64")]
       → Returns: {verified: true, mismatched_fields: []}
Agent: [calls call_facilities_thinker_via_api(...)]
       → Creates service request
Agent: "I've created service request SR-12345..."
```

### Successful Verification (Voice — rent with birth year)

```
User: "What's my rent balance?"
Agent: "For security, could you confirm your unit number and birth year?"
User: "Unit 64, born in 1990"
Agent: [calls verify_resident_identity(unit_number="64", birth_year="1990")]
       → Returns: {verified: true, mismatched_fields: []}
Agent: [calls get_rent_information(...)]
       → Returns balance
Agent: "Your current balance is $1,250.00..."
```

### SMS Partial Verification (unit verified, birth year needed for rent)

```
User: "I reported a leak earlier, what's the status? Also what's my balance?"
Agent: [identity_verified=true from earlier in session]
       [calls get_active_service_requests(...)]
       → Returns active requests
Agent: "Here are your active requests... For your balance, I'll need your
        birth year for security. Could you provide that?"
User: "1990"
Agent: [calls verify_resident_identity(unit_number="...", birth_year="1990")]
       → Returns: {verified: true, mismatched_fields: []}
Agent: [calls get_rent_information(...)]
       → Returns balance
```

### Session Persistence

Once verified, `identity_verified=true` on SessionScope. The agent knows not to re-verify:

```
User: "I need a parking pass"
Agent: [identity_verified=true from earlier in session]
       [calls issue_guest_parking_pass(...)]
       → Issues parking pass
Agent: "I've issued the parking pass..."
```

## Adding New Protected Tools

To protect a new tool:

1. **Update `verification_check.py`**: Add the tool to `PROTECTED_TOOLS` dict with its birth year requirement:
   ```python
   PROTECTED_TOOLS = {
       ...
       "new_tool_name": False,  # False = unit only, True = unit + birth year
   }
   ```

2. **For MCP tools**: Add the verification pre-processor in `agent.py`:
   ```python
   if channel != "CHAT" and "new_tool_name" in enabled_tools:
       pre_processor_extras["new_tool_name"] = [
           verification_pre_processor("new_tool_name")
       ]
   ```

3. **If the tool is also prefetched** via `call_and_save_tool` in `agent_helper.py`, pass `skip_pre_processors=True` at that call site so the prefetch succeeds before the resident has verified.

## Security Considerations

1. **No value exposure**: The verification tool never returns expected values, only whether the match succeeded and which fields failed.
2. **Defense in depth**: MCP pre-processors reject unverified tool calls as the primary enforcement. Prompt instructions serve as the secondary layer, guiding the LLM to verify proactively. Local tools (`call_facilities_thinker_via_api`) rely on prompt instructions only.
3. **Prefetch bypass**: Internal prefetch calls (e.g. `call_and_save_tool` in `agent_helper.py`) pass `skip_pre_processors=True` to `CachingMCPServer.call_tool` to bypass the verification pre-processor. This is intentional — prefetch runs before the resident has had a chance to verify, and the data is injected into the agent's prompt context, not returned directly to the user. Only use `skip_pre_processors=True` for system-initiated prefetch calls, never for agent-driven tool calls.
4. **Session isolation**: Verification status flags (`identity_verified`, `identity_verified_with_birth_year`) are stored on `SessionScope`, which is per-session. Verification does not persist across sessions.
5. **Normalization**: Unit number comparison is forgiving (case-insensitive, removes spaces and common prefixes) to reduce false negatives from minor formatting differences.
