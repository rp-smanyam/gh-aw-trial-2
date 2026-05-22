from agents import Runner

from agent_leasing.agent.resident_one_agent.agent import ResidentAgent


class TestLanguageCode:
    async def test_en(self, resident_context_unified_chat_ll):
        input_text = "Hello, how are you?"
        expected_output = "en"

        async with ResidentAgent(resident_context_unified_chat_ll) as resident_agent:
            result = await Runner.run(
                resident_agent.agent_instance,
                input_text,
                context=resident_context_unified_chat_ll,
            )

        assert result.final_output.language_code == expected_output

    async def test_es(self, resident_context_unified_chat_ll):
        input_text = "Hola, ¿cómo estás?"
        expected_output = "es"

        async with ResidentAgent(resident_context_unified_chat_ll) as resident_agent:
            result = await Runner.run(
                resident_agent.agent_instance,
                input_text,
                context=resident_context_unified_chat_ll,
            )

        assert result.final_output.language_code == expected_output
