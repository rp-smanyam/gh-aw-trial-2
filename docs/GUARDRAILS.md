# Agent Guardrails

This document describes guardrail implementations for the agent-leasing system. Guardrails are detective controls that help ensure agents behave appropriately and safely.

## Creating New Guardrails

To create a new guardrail:

1. Create a new Python file in this directory
2. Implement the guardrail function with the appropriate decorator:
   - `@input_guardrail` for input validation
   - `@output_guardrail` for output validation
3. Return a `GuardrailFunctionOutput` with:
   - `output_info`: The processed output or metadata
   - `tripwire_triggered`: Whether to block the operation
4. Add comprehensive tests
5. Document usage and behavior

Example structure:
```python
from agents import output_guardrail, GuardrailFunctionOutput

@output_guardrail
async def my_guardrail(ctx, agent, output):
    # Validation logic here
    if should_block:
        return GuardrailFunctionOutput(
            output_info="Safe response",
            tripwire_triggered=True
        )
    return GuardrailFunctionOutput(
        output_info=output,
        tripwire_triggered=False
    )
```

# Guardrails List

## Competitor Blocking Guardrail

Actual output structure:

- When guardrail is triggered:
  - GuardrailFunctionOutput(
        output_info=CompetitorBlockingGuardrailOutput(
            reasoning="Detected competitor mentions: competitor1, competitor2",
            safe_response="I'm here to help you with your leasing needs. How can I assist you today?",
            detected_competitors=["competitor1", "competitor2"],
        ),
        tripwire_triggered=True,
    )
- When guardrail is not triggered:
  - GuardrailFunctionOutput(
        output_info=output,
        tripwire_triggered=False,
    )

Implementation notes:

- Source: `src/agent_leasing/agent/guardrails/competitor_blocking_guardrail_agent/competitor_blocking_guardrail_agent.py`
- Wired into: Applicant and Resident agents (`src/agent_leasing/agent/applicant/agent.py`, `src/agent_leasing/agent/resident_one_agent/agent.py`)
- Configuration: competitor list and block message live in `competitors.json`

## Fair Housing Guardrail

Actual output structure:

- When guardrail is triggered:
  - GuardrailFunctionOutput(
        output_info=FairHousingOutputGuardrailOutput(
            reasoning="Response contained content disallowed by Fair Housing policy.",
            safe_response="I cannot make judgments about groups of people.",
            category="race",
        ),
        tripwire_triggered=True,
    ).
- When guardrail is not triggered:
  - GuardrailFunctionOutput(
        output_info=original_content,
        tripwire_triggered=False,
    )

Implementation notes:

- Source: `src/agent_leasing/agent/guardrails/fair_housing_guardrail_agent/fair_housing_guardrail_agent.py`
- Wired into: Applicant and Resident agents (input + output)
- Guardrail agent prompt lives in `FAIR_HOUSING_GUARDRAIL_AGENT.md`
- Tests: `tests/integration/agent/guardrails/test_fair_housing_guardrail_agent.py`

## PII Guardrail

Actual output structure:

- When guardrail is triggered:
  - GuardrailFunctionOutput(
        output_info=PIIGuardrailOutput(
            reasoning="Found PII types: email address, phone number",
            safe_response="I'm sorry, but I cannot process requests containing personal information.",
            pii_types_found=["email address", "phone number"],
        ),
        tripwire_triggered=True,
    )
- When guardrail is not triggered:
  - GuardrailFunctionOutput(
        output_info=original_content,
        tripwire_triggered=False,
    )

Implementation notes:

- Source: `src/agent_leasing/agent/guardrails/pii_output_guardrail/pii_guardrail.py`
- Wired into: Applicant agent (output) and Resident agent (input + output)
- Detection backend: Microsoft Presidio with custom recognizers (credit card & phone formats)
- Tests: `tests/integration/agent/guardrails/test_security_output_guardrail.py` validates safe pass-through, additional scenarios live with agent tests

## Security Guardrail

Actual output structure (identical for input and output triggers):

- When guardrail is triggered:
  - GuardrailFunctionOutput(
        output_info=SecurityGuardrailOutput(
            reasoning="Flagged categories: violence, illicit",
            is_harmful=True,
            flagged_categories=["violence", "illicit"],
            output="I cannot provide information or advice about illegal or harmful activities.",
        ),
        tripwire_triggered=True,
    )
- When guardrail is not triggered:
  - GuardrailFunctionOutput(
        output_info=original_content,
        tripwire_triggered=False,
    )

Implementation notes:

- Source: `src/agent_leasing/agent/guardrails/security_guardrail_agent/security_guardrail_agent.py`
- Wired into: Applicant agent (input), Resident agent (output)
- Backend: OpenAI Moderations API using thresholds from `settings.security_guardrail_thresholds`
- Tests: `tests/integration/agent/guardrails/test_security_guardrail_agent.py`

## Prisma AIRS Guardrail

Actual output structure (identical for input and output triggers):

- When guardrail is triggered:
  - GuardrailFunctionOutput(
        output_info=PrismaAirsGuardrailOutput(
            reasoning="Prisma AIRS recommended action: block",
            is_harmful=True,
            flagged_categories=["injection", "dlp"],
            safe_response="I'm sorry, but I cannot provide information or advice about offensive, illegal, or harmful activities. How else can I assist you today?",
        ),
        tripwire_triggered=True,  # Only if settings.prisma_airs_blocking_mode is enabled
    )
- When guardrail is not triggered:
  - GuardrailFunctionOutput(
        output_info=original_content,
        tripwire_triggered=False,
    )

Implementation notes:

- Source: `src/agent_leasing/agent/guardrails/prisma_airs_guardrail_agent/prisma_airs_guardrail_agent.py`
- Wired into: All agents (input and output) when enabled
- Backend: Palo Alto Networks Prisma AI Runtime Security (AIRS) API
- Configuration:
  - `settings.prisma_airs_api_url`: API endpoint URL
  - `settings.prisma_airs_api_key`: API authentication key
  - `settings.prisma_airs_profile_name`: Security profile to use
  - `settings.prisma_airs_blocking_mode`: Enable/disable tripwire (allows monitoring without blocking)
  - `settings.enabled_input_guardrails`: Add "prisma_airs" to enable for inputs
  - `settings.enabled_output_guardrails`: Add "prisma_airs" to enable for outputs
- Features:
  - Detects prompt injection attempts, data leakage (DLP), and malicious URLs
  - Supports both input (user prompts) and output (agent responses) scanning
  - Monitoring mode: Can detect violations without blocking when `prisma_airs_blocking_mode=False`
  - Includes architecture-aware model metadata in API requests
- Tests: `tests/unit/agent/guardrails/test_prisma_airs_guardrail.py`

## Unauthorized Promises Guardrail

Actual output structure:

- When guardrail is triggered:
  - GuardrailFunctionOutput(
        output_info=UnauthorizedPromisesGuardrailOutput(
            reasoning="Response contained content disallowed by Unauthorized Promises policy.",
            safe_response="I'm not authorized to make that commitment. Let me connect you with someone who can help with that request.",
            is_promise=True,
        ),
        tripwire_triggered=True,
    )
- When guardrail is not triggered:
  - GuardrailFunctionOutput(
        output_info=original_content,
        tripwire_triggered=False,
    )

Implementation notes:

- Source: `src/agent_leasing/agent/guardrails/unauthorized_promises_guardrail_agent/unauthorized_promises_guardrail_agent.py`
- Guardrail agent prompt lives in `src/agent_leasing/agent/guardrails/unauthorized_promises_guardrail_agent/UNAUTHORIZED_PROMISES_GUARDRAIL_AGENT.md`
- Configuration: add "unauthorized_promises" to `settings.enabled_output_guardrails`
- Safe response is localized via `localize_guardrail_response`
- Tests: `tests/unit/agent/guardrails/test_unauthorized_promises_guardrail_heuristic.py`, `tests/integration/agent/guardrails/test_unauthorized_promises_guardrail_agent.py`

## Legal Advice Guardrail

Actual output structure:

- When guardrail is triggered:
  - GuardrailFunctionOutput(
        output_info=LegalAdviceGuardrailOutput(
            reasoning="Response contained content disallowed by Legal Advice policy.",
            safe_response="I'm not able to provide legal advice or interpret legal matters. Please consult a qualified attorney for guidance. I can help with general property information or point you to your lease terms.",
            is_legal_advice=True,
        ),
        tripwire_triggered=True,
    )
- When guardrail is not triggered:
  - GuardrailFunctionOutput(
        output_info=original_content,
        tripwire_triggered=False,
    )

Implementation notes:

- Source: `src/agent_leasing/agent/guardrails/legal_advice_guardrail_agent/legal_advice_guardrail_agent.py`
- Guardrail agent prompt lives in `src/agent_leasing/agent/guardrails/legal_advice_guardrail_agent/LEGAL_ADVICE_GUARDRAIL_AGENT.md`
- Configuration: add "legal_advice" to `settings.enabled_output_guardrails`
- Safe response is localized via `localize_guardrail_response`
- Tests: `tests/unit/agent/guardrails/test_legal_advice_guardrail_heuristic.py`, `tests/integration/agent/guardrails/test_legal_advice_guardrail_agent.py`
