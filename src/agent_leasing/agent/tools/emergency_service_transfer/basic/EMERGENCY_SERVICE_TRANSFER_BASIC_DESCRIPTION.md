# Emergency Service Transfer

Connects the resident to the emergency maintenance line by looking up the emergency number in the knowledge base and then transferring the call OR providing the number for the resident to call directly.

## Trigger:
- An emergency maintenance request has been attempted (success OR failure)

## Required Parameters

1. **already_created_emergency_service_request** (bool, required) – Set to True if you have already attempted to create the service request (regardless of whether it succeeded or failed). Set to False only if no service request attempt was made at all.
2. **service_request_summary** (str, required) – A clear 1-2 sentence description of the emergency that was the basis for the prior `create_service_request` call. Reuse the same wording you passed as `chat_summary` to that tool. Include location and access details where known (e.g., "Water leaking through the ceiling in unit 304; resident says it's spreading. Front gate code 1234.").

## Steps:
1. Verify that the emergency service request has already been attempted.
  - **CRITICAL**: If the emergency service request failed or returned an error, you MUST still proceed. Set `already_created_emergency_service_request=True` because an attempt was made.
2. If the channel is voice, 
  - ensure the resident has already heard non-empty assistant speech before this tool call,
  - ensure that speech included the emergency safety guidance and service request status,
  - ensure a brief pre-transfer announcement was spoken before this tool call,
  - do NOT wait for the resident to answer a follow-up question before calling this tool.

  Then immediately call `emergency_service_transfer_basic(already_created_emergency_service_request=True, service_request_summary="<the same summary you used for create_service_request>")` to connect them with the emergency technician phone line.
**CRITICAL**: Connecting the resident to the emergency line is MORE important than the service request. Even if SR creation failed, this tool MUST still be called.

## Tool Return Value

The `emergency_service_transfer_basic` tool returns: `{message: str}` containing a confirmation message about the transfer status.