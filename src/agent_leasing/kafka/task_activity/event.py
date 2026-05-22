"""TaskActivityEvent envelope shape.

One pure function that owns the Avro envelope structure. Every extractor
goes through it so the on-wire shape stays consistent across activity
types and a future schema change touches one place.
"""

TASK_CODE_RESIDENT_CONVERSATION = "RESIDENT_CONVERSATION"
TASK_NAME_RESIDENT_CONVERSATIONS = "Resident Conversations"
TASK_DOMAIN_RESIDENT = "RESIDENT"


def build_task_activity_event(
    *,
    task_id: str,
    activity_summary: str,
    activity_detail: str,
    references: list[dict],
    extra: dict[str, str],
) -> dict:
    """Return a TaskActivityEvent dict ready for the Avro serializer."""
    if not task_id:
        raise ValueError("task_id must be a non-empty string")
    return {
        "task": {
            "id": task_id,
            "code": TASK_CODE_RESIDENT_CONVERSATION,
            "name": TASK_NAME_RESIDENT_CONVERSATIONS,
            "domain": TASK_DOMAIN_RESIDENT,
        },
        "activity": {
            "summary": activity_summary,
            "detail": activity_detail,
        },
        "references": references,
        # `extra` is a `["null", map<string,string>]` union in the schema —
        # fastavro auto-detects the branch from the value type, so the map
        # goes through as a bare dict (no `{"map": ...}` wrapper).
        "extra": extra,
    }
