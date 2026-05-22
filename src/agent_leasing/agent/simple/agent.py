import os

import jinja2
from agents import Agent, RunContextWrapper
from agents.mcp import MCPServerStreamableHttp
from agents.realtime import RealtimeAgent

from agent_leasing.agent.hooks import RenterAIAgentHooks
from agent_leasing.agent.util import AgentWithMCP, SessionScope
from agent_leasing.settings import build_model_settings, settings

agent_hooks = RenterAIAgentHooks()

PROMPT_FILE = "PROMPT.md"


class SimpleAgent(AgentWithMCP):
    """Simple agent implementation provided for testing."""

    def __init__(self, context, real_time: bool = False):
        super().__init__(context)
        self.real_time = real_time
        self.prompt = self._get_prompt(os.path.join(os.path.dirname(__file__), PROMPT_FILE))

        self.property_mcp_server = MCPServerStreamableHttp(
            name="Caching Property MCP Server",
            params={"url": settings.knock_mcp_server, "headers": {}},
            cache_tools_list=True,
            client_session_timeout_seconds=10,
        )
        self.mcp_servers = {"property_mcp_server": self.property_mcp_server}

    # __aenter__ and __aexit__ are now implemented in the AgentWithMCP base class.

    async def _create_agent(self):
        if self.real_time:
            return RealtimeAgent(
                name="Realtime Simple Agent",
                instructions=self._get_agent_instructions,  # noqa
                hooks=agent_hooks,
                # mcp_servers=[self.property_mcp_server],
            )
        else:
            return Agent(
                name="Simple Agent",
                instructions=self._get_agent_instructions,
                model=settings.model,
                model_settings=build_model_settings(
                    model=settings.model,
                    effort=settings.model_reasoning_effort,
                    verbosity=settings.model_verbosity,
                    temperature=settings.model_temperature,
                    service_tier=settings.model_service_tier,
                ),
                hooks=agent_hooks,
                # mcp_servers=[self.property_mcp_server],
            )

    def agent(self) -> Agent | RealtimeAgent:
        return self.agent_instance

    async def _get_agent_instructions(
        self,
        run_context: RunContextWrapper[SessionScope],
        agent: Agent[SessionScope],  # noqa
    ) -> str:
        """Gets instructions for the agent and injects data from the context."""

        environment = jinja2.Environment()
        template = environment.from_string(self.prompt)
        return template.render(
            current_time=run_context.context.current_time.isoformat(),
            context=run_context.context,
        )
