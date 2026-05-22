import pytest
from agents.realtime import RealtimeRunner

from agent_leasing.agent.resident_one_agent.realtime import ResidentRealtimeResponderAgent
from tests.integration.helpers import (
    build_realtime_test_model_config,
    get_realtime_response,
)


class TestResidentRealtimeResponderAgent:
    @pytest.mark.asyncio
    async def test_real_time_responder_agent(self, resident_context_voice_knck):
        """Test normal realtime responder agent behavior."""
        async with ResidentRealtimeResponderAgent(resident_context_voice_knck) as resident_responder_agent:
            runner = RealtimeRunner(resident_responder_agent.agent_instance)
            session_context = await runner.run(
                context=resident_context_voice_knck,
                model_config=build_realtime_test_model_config(),
            )
            async with session_context as session:
                response_text = await get_realtime_response(session, "Hello")
                assert response_text
