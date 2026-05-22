"""Filler message templates — the three-tier system plus handoff.

Message templates from ``twilio_handler.py`` module-level constants.

Tier 1 (thinker active): "still working on it" — patient, brief.
Tier 2 (idle, below threshold): "still here" — gentle nudge toward thinker.
Tier 3 (escalation, at/above threshold): CRITICAL — forces model to review
        and call the thinker tool or acknowledge completion.
Handoff: Transfer in progress — focused on transfer_to_staff_voice.
"""

FILLER_HANDOFF_MESSAGE = """
**IMPORTANT**: Do not acknowledge this message.
A transfer to staff is in progress — the caller was just asked to provide a summary.
Respond in {language_code}.
- If the caller provided any informative response (even brief e.g., "billing question", "maintenance issue" or vague e.g., "speak", "question", "issue"), call `transfer_to_staff_voice` with that summary NOW — do NOT evaluate quality or ask again.
- If the caller refused or deflected (e.g., "no", "just connect me"), call `transfer_to_staff_voice(summary=None)` NOW.
- If the caller explicitly cancelled the transfer (e.g., "never mind", "don't bother", "I don't want to be transferred"), do NOT call this tool — respond with "Okay, how else can I assist you?" and return to normal conversation.
- If the caller has not responded (silence), call `transfer_to_staff_voice(summary=None)` NOW to honor the original transfer request. Do NOT re-ask the summary question.
Do NOT call any other tools. Do NOT change the subject. Stay focused on the transfer.
"""

FILLER_THINKER_ACTIVE_MESSAGE = """
**IMPORTANT**: Do not acknowledge this message.
Just send a short, natural filler line in {language_code}.
One sentence only, at most 12 words. Do NOT reuse the exact wording of the previous filler in this conversation.
We are waiting for a tool or internal action to finish. Deliver a fresh paraphrase of:
`I'm still working on that—it'll just be a little bit longer.`
"""

FILLER_IDLE_MESSAGE = """
**IMPORTANT**: Do not acknowledge this message.
Just send a short, natural filler line in {language_code}.
One sentence only, at most 12 words. Do NOT reuse the exact wording of the previous filler in this conversation.
1) If the resident asked a question or made a request that hasn't been addressed yet, call `resident_thinker_tool` with a summary of their request instead of sending a filler.
2) Otherwise, deliver a new paraphrase of:
`I'm still here for you—let me know if there's anything else I can help with.`
"""

FILLER_ESCALATION_MESSAGE = """
**CRITICAL**: You have been sending filler messages for a while. Review the conversation NOW.
Respond in {language_code}.
- If the resident provided information, asked a question, or made a request that hasn't been fully resolved \
with a tool call, you MUST call `resident_thinker_tool` NOW with a summary of their request.
- If the resident's last request was already completed and you are waiting for them to speak, say: \
"I'm still here — is there anything else I can help you with?"
Do NOT send another "please wait" filler unless a tool is actively processing.
"""

GUARDRAIL_TRIPPED_MESSAGE = (
    "The following guardrails were tripped:\n{guardrail_message}.\n"
    "Respond to the user with a creative variation of the following in {language_code}: "
    "I cannot answer the previous question. How else can I help you?"
)
