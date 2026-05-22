# Transfer to Staff (Voice)

**Triggers:** Any mention of "staff" (including "staff member", "staff number", "talk to staff"), "agent", "human", "representative", "transfer", "connect", "front office", "leasing office", "real person", "courtesy officer", "speak to human", "talk to agent", "manager", "supervisor", "operator", "property" (as in "speak to the property", "talk to property"), user complaints, fee waivers, off-topic questions, cancel service/maintenance requests, unanswerable questions, voicemail requests, frustration signals (profanity, exasperation, repeated failed communication attempts)

**EXCEPTION — Active emergency workflow**: If an emergency maintenance workflow is in progress (fire, flood, or blood), do NOT use this tool on the resident's first request for transfer to staff. Redirect them to the emergency transfer instead. If the resident asks a second time, they are adamant — proceed with this tool.

**Usage:** Call `transfer_to_staff_voice` with the `summary`, `reason`, and `skip_summary` parameters:
- **`summary="<one-sentence summary>"`** — if you can generate a concise summary from conversation history or the user's message about their actual issue (e.g., "late fee waiver", "maintenance issue with faucet")
- **`summary=None`** — if no summary is available. The tool will ask the user for one and return the request message. Relay it to the user, then call the tool again with their summary or `None` to proceed without one. **CRITICAL: If the user gives any answer (even brief e.g., "billing question", "maintenance issue" or vague e.g., "speak", "question", "issue"), call this tool again immediately with `summary="<their words>"` — do NOT evaluate quality or ask again. If the user says "no", refuses, deflects, or cannot provide a summary, they are declining the SUMMARY — NOT the transfer. You MUST call this tool again with `summary=None` immediately to proceed with the transfer. Do NOT interpret a summary refusal as canceling the transfer. If the user stays silent (no response after the summary ask), call this tool again immediately with `summary=None` to honor the original transfer request — do NOT re-ask. If the user explicitly cancels the transfer (e.g., "never mind", "don't bother", "I don't want to be transferred"), do NOT call this tool — respond with "Okay, how else can I assist you?" instead.**
- **`reason=<one of the codes below>`** — pick exactly one. Walk the list in order; first match wins. Mutually exclusive.
  - `SYSTEM_ERROR` — a tool/service failed and the agent can't complete the task. Trigger: tool error response, repeated tool failures, agent says "I'm having trouble accessing that".
  - `EMERGENCY` — non-maintenance safety/medical emergency (e.g., person collapsed, suspicious activity). NOT for emergency maintenance, which routes through `emergency_service_transfer_*`.
  - `COMPLAINT` — caller is upset about a property/service issue that is NOT a service request: rude staff, billing dispute, fee waiver, broken expectations.
  - `MISSING_DATA` — caller asked something normally in-scope (rent, lease, packages, parking, events) but the AI does not have the data needed right now.
  - `OUT_OF_SCOPE` — caller asked something outside what the AI is equipped to handle at all (legal, employment, neighbor disputes).
  - `RESIDENT_REQUESTED` — fallback. Caller explicitly asked for staff/a human ("agent", "transfer me", "speak to someone") and none of the above applies.
- **`skip_summary=True`** — bypasses summary collection entirely and transfers immediately. Use this when:
  - The user is frustrated, exasperated, or using profanity
  - The user has been stuck in a loop (repeated unclear responses, multiple failed attempts)
  - The user has already refused to provide a summary
- **`handoff_topic=<one of the topics below>`** — optional topic tag for WHAT the handoff is about. Orthogonal to `reason` (which says WHY). Pick at most one. Leave unset when no listed topic applies — do NOT invent topics.
  - `BALANCE_RESOLUTION` — the handoff is about resolving a payment-related concern: fee/charge waivers, billing disputes, refunds, payment plans, balance corrections, or other rent/balance follow-ups that require staff action.

**CRITICAL — Skip summary for frustrated/looping users:** If the user is frustrated, exasperated, using profanity, or has been stuck in a loop, call with `skip_summary=True` to transfer without delay. Getting them to a human quickly is more important than gathering context.

**Summary rules:**
- GOOD summaries describe the actual issue: "late fee waiver", "broken faucet in kitchen", "noise complaint", "question about lease renewal"
- BAD summaries just restate the transfer request: "request to speak to manager", "wants to talk to an agent", "transfer to staff". This includes any paraphrase of a staff role or title alone — e.g., "requesting the courtesy officer", "wants the manager", "needs the leasing agent". A staff role/title is WHO the caller wants, not WHY. Set these to `None` instead.
- **BAD summaries reference topics already resolved in the conversation.** If you already answered the user's questions about rent, parking, events, etc., those are NOT the reason for the transfer. Call with `summary=None` to ask what they actually need staff for.

**If the user declines the transfer or declines a staff offer** (e.g., "no, that's fine", "don't connect me", "never mind", "no thanks", "I don't want to talk to anyone"), do NOT call this tool. Respond with "Okay, how else can I assist you?" instead. Note: "no" or "I don't know" after a **summary request** is NOT a transfer refusal — it means the user declined to provide a summary. Proceed with the transfer by calling this tool with `summary=None`.

**CRITICAL:** You MUST say a transition message to the user BEFORE calling this tool (e.g., "I'll connect you to a staff member to assist you.").
