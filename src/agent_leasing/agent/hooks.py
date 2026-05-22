from typing import Any

from agents import Agent, AgentHooks, RunContextWrapper, TContext, Tool


class RenterAIAgentHooks(AgentHooks):
    async def on_start(self, context: RunContextWrapper[TContext], agent: Agent[TContext]) -> None:
        pass

    async def on_end(
        self,
        context: RunContextWrapper[TContext],
        agent: Agent[TContext],
        output: Any,
    ) -> None:
        pass

    async def on_tool_start(
        self,
        context: RunContextWrapper[TContext],
        agent: Agent[TContext],
        tool: Tool,
    ) -> None:
        """
        Called before a tool is invoked.

        Not currently used except for testing, where it proves quite
        useful to check if tool calls have been made.
        """
        pass

    async def on_tool_end(
        self,
        context: RunContextWrapper[TContext],
        agent: Agent[TContext],
        tool: Tool,
        result: str,
    ) -> None:
        """Called after a tool is invoked.

        Not currently used except for testing, where it proves quite
        useful to check if tool calls have been made.
        """
        pass
