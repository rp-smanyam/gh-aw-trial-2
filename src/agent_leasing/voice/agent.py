"""VoiceAgent — creates the voice responder agent with clean handler integration.

Unlike the legacy path where ``ResidentRealtimeResponderAgent`` is created
and coupled to twilio_handler via ``ctx._session_handler``, VoiceAgent
uses the existing ``ResidentRealtimeResponderAgent`` for MCP/thinker setup
but replaces the thinker tool with one that uses ``VoiceCallbacks``.
"""

from __future__ import annotations

import structlog
from agents.realtime import RealtimeAgent

from agent_leasing.agent.resident_one_agent.realtime import (
    ResidentRealtimeResponderAgent,
)
from agent_leasing.agent.tools import (
    end_call,
    get_emergency_service_transfer_fxn,
    set_conversation_language,
    transfer_to_staff_voice,
)
from agent_leasing.agent.util import SessionScope, get_enabled_output_guardrails
from agent_leasing.voice.callbacks import VoiceCallbacks
from agent_leasing.voice.config import VoiceConfig
from agent_leasing.voice.coordination.call_state import VoiceCallState
from agent_leasing.voice.thinker.tool import create_voice_thinker_tool

logger = structlog.get_logger(__name__)


class VoiceAgent:
    """Wraps ``ResidentRealtimeResponderAgent`` with VoiceCallbacks integration.

    Reuses the existing agent class for MCP connection sharing, prompt
    rendering, and thinker agent initialization.  Overrides the thinker
    tool creation to use ``create_voice_thinker_tool`` (which uses
    ``VoiceCallbacks`` instead of ``ctx._session_handler``).

    Lifecycle::

        agent = VoiceAgent(ctx, callbacks, call_state, config)
        await agent.setup()           # creates responder + thinker
        realtime_agent = agent.agent()  # the RealtimeAgent for the session
        await agent.cleanup()         # close MCP connections
    """

    def __init__(
        self,
        ctx: SessionScope,
        callbacks: VoiceCallbacks,
        call_state: VoiceCallState,
        config: VoiceConfig,
    ) -> None:
        self._ctx = ctx
        self._callbacks = callbacks
        self._call_state = call_state
        self._config = config

        self._responder: ResidentRealtimeResponderAgent | None = None
        self._realtime_agent: RealtimeAgent | None = None

    async def setup(self) -> RealtimeAgent:
        """Create the responder agent with a thinker tool wired to callbacks.

        1. Create ``ResidentRealtimeResponderAgent`` (handles MCP, prefetch, prompts)
        2. Enter the responder (``__aenter__``) — this sets up MCP + thinker
        3. Replace the thinker tool with our VoiceCallbacks-based version
        4. Return the ``RealtimeAgent`` for the session

        The key difference from the legacy path: the responder's ``_create_agent``
        is called by ``__aenter__`` and uses the original ``create_thinker_tool``.
        We then rebuild the tools list with our version.
        """
        self._responder = ResidentRealtimeResponderAgent(self._ctx)
        await self._responder.__aenter__()

        # Get the thinker agent that was initialized during __aenter__
        thinker_agent = self._responder._thinker_agent

        # Create our thinker tool (uses VoiceCallbacks, not ctx._session_handler)
        voice_thinker_tool = create_voice_thinker_tool(
            context=self._ctx,
            thinker_agent=thinker_agent,
            callbacks=self._callbacks,
            call_state=self._call_state,
            config=self._config,
        )

        # Rebuild the RealtimeAgent with our thinker tool
        from agent_leasing.agent.hooks import RenterAIAgentHooks

        tools_list = [
            voice_thinker_tool,
            end_call,
            transfer_to_staff_voice,
            set_conversation_language,
            get_emergency_service_transfer_fxn(
                self._ctx.ask_request.emergency_service_product,
                context=self._ctx,
            ),
        ]

        self._realtime_agent = RealtimeAgent(
            name="Realtime Resident Agent (One)",
            handoff_description="Handle all voice communications; delegate to thinker agent tool for everything else.",
            instructions=self._responder._get_responder_instructions,
            hooks=RenterAIAgentHooks(),
            tools=tools_list,
            output_guardrails=get_enabled_output_guardrails(),
        )

        return self._realtime_agent

    def agent(self) -> RealtimeAgent:
        """Return the RealtimeAgent (must call ``setup()`` first)."""
        assert self._realtime_agent is not None, "call setup() first"
        return self._realtime_agent

    async def cleanup(self) -> None:
        """Close the responder's MCP connections."""
        if self._responder:
            try:
                await self._responder.__aexit__(None, None, None)
            except Exception as e:
                logger.warning(f"Error cleaning up voice agent: {e}")
            finally:
                self._responder = None
                self._realtime_agent = None
