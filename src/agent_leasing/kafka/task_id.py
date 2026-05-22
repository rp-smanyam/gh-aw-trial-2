from typing import TYPE_CHECKING
from uuid import UUID, uuid5

if TYPE_CHECKING:
    from agent_leasing.models.context import SessionScope

# Deterministic UUID namespace for task-activity-event `task.id` derivation.
# Shared with the sibling task-event ticket so both topics carry the same
# UUID per conversation. Changing this value breaks that cross-topic join.
AGENT_LEASING_TASK_NAMESPACE = UUID("3c9f8a7b-2e1d-4f6a-8b5c-0d9e7f3a6c1b")


def build_task_id(channel: str, conversation_key: str) -> str:
    """Return a deterministic UUID string for a conversation's task.id.

    Pure UUID composition. Callers should derive ``channel`` and
    ``conversation_key`` from a SessionScope via ``derive_conversation_key``
    so every producer (task-event, task-activity-event, ...) ends up with
    the same task.id per conversation.
    """
    if not channel:
        raise ValueError("channel must be a non-empty string")
    if not conversation_key:
        raise ValueError("conversation_key must be a non-empty string")
    return str(uuid5(AGENT_LEASING_TASK_NAMESPACE, f"{channel}:{conversation_key}"))


def derive_conversation_key(ctx: "SessionScope") -> tuple[str, str]:
    """Return ``(channel, conversation_key)`` for task.id derivation.

    Single source of truth shared by every Kafka topic that publishes
    task.id-keyed events (task-event today; task-activity-event in
    KNCK-39556 PR 2). Keeping this in one place guarantees both topics
    can be joined on task.id.

    VOICE / CHAT — chat_session_id is the natural session boundary:
        * VOICE: one chat_session_id per call.
        * CHAT: upstream rotates chat_session_id when the 10-minute Redis
          cache entry expires.

    SMS / EMAIL — chat_session_id is upstream's stream_id, which is
    person-level (one ID across many sessions for the same person).
    Combine it with ``ctx.session_marker`` (regenerates on Redis cache
    miss) so each session for the same person gets a distinct task.id.

    Both ``ctx.ask_request`` and ``ctx.ask_request.chat_session_id`` are
    invariants of every code path that reaches this function (chat_session_id
    has a UUID default factory on AskRequest); we let the AttributeError
    surface loud rather than fabricating an orphan key on a violated invariant.
    """
    channel = ctx.ask_request.conversation_type.value.upper()
    chat_session_id = ctx.ask_request.chat_session_id

    if channel in {"VOICE", "CHAT"}:
        return channel, chat_session_id
    return channel, f"{chat_session_id}:{ctx.session_marker}"
