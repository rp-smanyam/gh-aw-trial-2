import pytest

from agent_leasing.kafka.task_activity.event import (
    TASK_CODE_RESIDENT_CONVERSATION,
    TASK_DOMAIN_RESIDENT,
    TASK_NAME_RESIDENT_CONVERSATIONS,
    build_task_activity_event,
)


class TestBuildTaskActivityEvent:
    def test_envelope_shape(self):
        event = build_task_activity_event(
            task_id="task-uuid",
            activity_summary="Create SR - Non-Emergency",
            activity_detail="Created a service request 123",
            references=[{"type": "COMPANY", "source": "KNCK", "id": "c-1"}],
            extra={"channel": "CHAT", "originating_source": "RESIDENT_AI"},
        )
        assert event["task"] == {
            "id": "task-uuid",
            "code": TASK_CODE_RESIDENT_CONVERSATION,
            "name": TASK_NAME_RESIDENT_CONVERSATIONS,
            "domain": TASK_DOMAIN_RESIDENT,
        }
        assert event["activity"] == {
            "summary": "Create SR - Non-Emergency",
            "detail": "Created a service request 123",
        }
        assert event["references"] == [{"type": "COMPANY", "source": "KNCK", "id": "c-1"}]
        # `extra` is the Avro `union { null, map<string> }`; fastavro
        # auto-detects the branch from the value type, so we ship the
        # bare dict.
        assert event["extra"] == {"channel": "CHAT", "originating_source": "RESIDENT_AI"}

    @pytest.mark.parametrize("task_id", ["", None])
    def test_raises_on_empty_task_id(self, task_id):
        with pytest.raises(ValueError):
            build_task_activity_event(
                task_id=task_id,
                activity_summary="x",
                activity_detail="y",
                references=[],
                extra={},
            )
