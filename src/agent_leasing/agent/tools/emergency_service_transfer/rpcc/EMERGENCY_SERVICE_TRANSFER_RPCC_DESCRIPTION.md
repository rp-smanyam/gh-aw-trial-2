# Emergency Service Transfer (RPCC)

Transfers the emergency to the RPCC maintenance team. Behavior depends on channel:
- **VOICE**: Redirects the live call to the RPCC maintenance team.
- **Non-voice** (SMS/EMAIL/CHAT): Initiates an outbound call to the resident's callback number and connects them to the RPCC maintenance team.

**CRITICAL: DO NOT create a service request before calling this tool. RPCC handles work order creation.**

## Trigger:
- The property uses RPCC for emergency maintenance
- The resident has reported a maintenance emergency
- You have collected emergency details from the resident

## Steps:
1. If needed, collect maintenance details from the resident — what happened, location, access notes, urgency.
{% if channel == 'VOICE' %}
2. Tell the resident: "I'll get you connected with someone from the property right away." This spoken message is the required non-empty assistant text before the tool call.
3. Call `emergency_service_transfer_rpcc` with a detailed summary of the emergency.
{% else %}
2. Ask: "I'll get you connected with someone from the property right away. What number should they use to reach you?"
3. Call `emergency_service_transfer_rpcc` with the summary and resident's phone number.
{% endif %}

## Required Parameters

1. **service_request_summary** (string, required) — A detailed description of the emergency including what happened, location, and access details. Be thorough since no service request was created.
{% if channel != 'VOICE' %}
2. **resident_phone** (string, required for non-voice) — The resident's callback phone number in E.164 format (e.g., +15555555555).
{% endif %}

## Tool Return Value

{% if channel == 'VOICE' %}
- **Success:** Confirmation that the call has been transferred to RPCC.
{% else %}
- **Success:** Confirmation that an outbound call has been initiated to the resident.
{% endif %}
- **Error:** A failure message indicating you should escalate to a human teammate.
