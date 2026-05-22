# Emergency Service Transfer

Reaches out to the on-call emergency technician via the emergency dispatch system. The system will attempt to call the technician, read them your AI-generated summary, and — if they answer — bridge them to the resident.

## Trigger:
- An emergency maintenance request has been attempted (success OR failure)

## Steps:
1. **Best-effort service request creation:** You should have ALREADY ATTEMPTED to create an emergency service request before calling this tool.
   - **CRITICAL:** Even if the service request creation failed or errored, you MUST still proceed with contacting the technician. Reaching the emergency line is MORE important than the service request in emergency situations.
   - Set `called_create_service_request=True` if you attempted to create the service request (regardless of success or failure).
   - Set `called_create_service_request=False` only if you haven't attempted to create a service request at all.

2. **Turn 1 — Safety + SR status + phone confirmation.** Respond with ALL of the following in a single response:
   - Briefly tell them to stay safe - just a single sentence, evacuate if needed, call 911 if needed. Keep this short; it's an emergency after all.
   - If the service request was created successfully, confirm request filed (include the service request ID). If it failed, mention you attempted to create one but no service request number was generated — do NOT invent one.
   - Confirm their callback phone number:
{% if context.ask_request.callback_number %}
     - The resident's phone number on file is {{ context.ask_request.callback_number }}.  Say this verbatim: "I have {{ context.ask_request.callback_number }} listed in the system. Is this the best number to reach you?"
{% else %}
     - Ask for the best callback phone number.
{% endif %}
   - **CRITICAL:** **ALWAYS** confirm this phone number. We need a verified callback number to reach the resident. This situation is an exception to the personal details rule — stating the resident’s phone number is permitted in emergencies.
   - **Then STOP and wait for the resident to respond.** Do NOT proceed until they confirm or provide a phone number.

3. **Turn 2 — Contact technician + tool call (after resident confirms phone).**
   - **Voice channel only:** Tell the user you are reaching out to the on-call emergency technician now. Do NOT promise the technician will call or arrive — only that you are connecting them. Ask if there is anything else they need help with. This spoken message is the required non-empty assistant text before the tool call. Set `already_played_voice_channel_transfer_message=True`.
   - **Non-voice channels:** Set `already_played_voice_channel_transfer_message=True` (this step doesn't apply).

4. **Contact:** Call `emergency_service_transfer_advanced` with:
   - The confirmed phone number in E.164 format
   - A concise 1-2 sentence summary of the emergency (include location + access notes)
   - The service request ID (if one was successfully created, otherwise pass None or omit it)


## Required Parameters


1. **resident_phone** (string, required) – The resident's confirmed callback phone number in E.164 format (e.g., +15555555555). This is where the technician will be connected after they pick up.

2. **service_request_summary** (string, required) – A clear 1-2 sentence description of the emergency that will be read to the technician. Include location and access details.

3. **service_request_id** (string or int, optional) – The ID of the emergency service request. If not provided, the tool will attempt to find the most recent service request from context.


## Tool Return Value

The tool returns a string with one of these outcomes:
- **Success:** A confirmation that the system is attempting to reach the on-call technician.
- **Phone validation error:** The phone number was invalid. Ask the resident to repeat their number and call the tool again.
- **Error:** A failure message indicating you should escalate to a human teammate.
