# Create Link

## Triggers:
- User requests access to a specific portal or service that CANNOT be solved by the Responder Agent or any of the attached Thinker Agents.
- User needs a link to perform an action (payments, amenities, parking, packages, etc.).

## Steps:
1. Identify the appropriate link type based on the user's request.
2. Call the `create_link` tool with the correct `link_type` parameter.
3. Return the generated link to the user in the appropriate format for the channel (SMS, CHAT, EMAIL, VOICE).
4. If the `create_link` tool call fails, inform the user of the error and suggest alternative assistance.

## Available Link Types:
- `payment_and_ledger` - Payment portal and account ledger
- `amenities` - Amenities reservation portal
- `reservations` - Current reservations portal
- `parking` - Guest parking passes management
- `package` - Package management portal
- `community_events` - Community events portal
- `human_hand_off` - Human handoff/messenger
- `service_request` - Service request portal
- `front_desk_instructions` - Front desk instructions
- `parking_passes` - Guest parking passes management
- `community_wall` - Community wall/bulletin board
- `single_service_request` - Single service request details. Pass `mr_id` with the service request ID to link directly to that request.
- `all_open_service_request` - All open service requests
- `leasing` - Lease documents and lease details. The link opens the resident portal homepage (NOT a "leasing portal") — include navigation directions: go to **"Manage My Apartment"** → **"My Lease"**.

If the specific path is not configured for a link type, the link will resolve to the portal homepage.

