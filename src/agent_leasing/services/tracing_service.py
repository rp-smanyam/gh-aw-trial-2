import time
import uuid

import structlog

logger = structlog.getLogger()


class OutputMessage:
    def __init__(self, data):
        self.data = data

    def model_dump(self):
        return self.data


class LangsmithResponseWrapper:
    """Lightweight stand-in for OpenAI Response objects to override trace outputs."""

    def __init__(self, output_text: str):
        self.instructions = ""
        self._output_text = output_text
        self._response_id = f"override-response-{uuid.uuid4()}"

    @property
    def id(self) -> str:
        return self._response_id

    def model_dump(self, *, exclude_none: bool = True, mode: str | None = None):  # noqa: ARG002
        response = {
            "id": self._response_id,
            "object": "response",
            "created_at": time.time(),
            "output": [
                {
                    "id": f"final-agent-response-{uuid.uuid4()}",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {
                            "type": "output_text",
                            "text": self._output_text,
                        }
                    ],
                }
            ],
            "parallel_tool_calls": False,
            "tool_choice": None,
            "tools": [],
        }

        if exclude_none:
            return {key: value for key, value in response.items() if value is not None}
        return response

    @property
    def output(self):
        return [
            OutputMessage(
                {
                    "id": f"final-agent-response-{uuid.uuid4()}",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {
                            "type": "output_text",
                            "text": self._output_text,
                        }
                    ],
                }
            )
        ]
