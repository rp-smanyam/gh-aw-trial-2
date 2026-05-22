# Safety-Critical Router

## Triggers:
- Agent encounters a potentially safety-critical resident request that requires routing to appropriate handling flows
- Resident reports emergencies, hazards, or urgent situations that may involve property systems or personal safety

## Function:
Routes safety-critical requests to the appropriate handling flow based on the nature of the emergency:
- **Maintenance emergencies** (gas leaks, fires, electrical hazards with clear and present danger to life, elevator failures in use, etc.) → Routes to Facilities Thinker flow
- **People/security emergencies** (violent threats, medical emergencies, criminal activity, etc.) → Routes to Handoff flow
- **Non-safety-critical requests** → Continues with standard workflows

## Parameters:
- `is_safety_critical`: Boolean indicating whether this is a safety-critical situation (e.g. fire, gas leak, suspicious person, threats, medical emergencies, etc.)
- `is_maintenance_related`: Boolean indicating whether the safety-critical situation involves property maintenance/systems (e.g. broken AC, leaky faucet, broken light, electrical outage, gas leak, etc.)

## Returns:
- "This is not a safety-critical request." - Continue with standard workflows
- "This is an emergency maintenance related request.  Follow the emergency maintenance flow." - Route to Facilities workflow
- "This is a safety-critical request. Follow the safety-critical Handoff flow." - Route to Handoff workflow

## Usage:
Call this tool when the agent determines a request may be safety-critical to get standardized routing instructions that ensure proper handling of emergencies and urgent situations.

