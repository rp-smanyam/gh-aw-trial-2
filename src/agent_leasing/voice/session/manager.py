"""SessionManager — owns the OpenAI RealtimeSession lifecycle.

Creates, configures, enters, and closes the OpenAI Realtime session.
Also owns the conversation history and exposes methods for sending
audio, messages, and events to the session.

This is a dedicated component — neither the transport nor the agent
owns it.  The handler wires it to both.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import structlog
from agents import gen_trace_id
from agents.realtime import (
    RealtimeInputAudioNoiseReductionConfig,
    RealtimeInputAudioTranscriptionConfig,
    RealtimeModelConfig,
    RealtimeModelSendInterrupt,
    RealtimeModelSendRawMessage,
    RealtimeModelTracingConfig,
    RealtimePlaybackTracker,
    RealtimeRunner,
    RealtimeSession,
    RealtimeSessionEvent,
    RealtimeSessionModelSettings,
    RealtimeTurnDetectionConfig,
)
from agents.realtime.config import RealtimeReasoningConfig
from agents.tracing.util import gen_group_id

from agent_leasing.settings import settings
from agent_leasing.util.realtime_util import realtime_history_to_input_list
from agent_leasing.voice.config import VoiceConfig

logger = structlog.get_logger(__name__)


class SessionManager:
    """Manages the OpenAI RealtimeSession for a single voice call.

    Lifecycle::

        sm = SessionManager(config)
        await sm.create(agent, context, metadata)
        await sm.enter()
        async for event in sm.events():
            ...
        await sm.close()
    """

    def __init__(self, config: VoiceConfig) -> None:
        self._config = config
        self.session: RealtimeSession | None = None
        self._session_ready = asyncio.Event()

        # The SDK's playback tracker (used for truncation on barge-in)
        self.playback_tracker = RealtimePlaybackTracker()

        # Conversation history
        self.history: list[Any] = []

        # SDK bug workaround (KNCK-38461): accumulate transcripts independently.
        # session._item_transcripts gets cleared on turn_ended before item_updated
        # can use them.  The transcript_cache survives across turns.
        self.transcript_cache: dict[str, str] = {}

        # Tracing identifiers
        self.trace_id: str = gen_trace_id()
        self.group_id: str = gen_group_id()

        # Stored for retry / recovery (set by create())
        self._model_config: RealtimeModelConfig | None = None
        self._metadata: dict[str, Any] = {}
        self._last_agent: Any = None
        self._last_context: Any = None

    # ------------------------------------------------------------------
    # Session creation
    # ------------------------------------------------------------------

    async def create(
        self,
        agent: Any,
        context: Any,
        metadata: dict[str, Any],
    ) -> None:
        """Create a new RealtimeSession (but don't enter it yet).

        Args:
            agent: The OpenAI Agents SDK ``RealtimeAgent``.
            context: The ``SessionScope`` context for this call.
            metadata: Metadata dict for tracing (environment, call-sid, etc.).
        """
        cfg = self._config
        self._metadata = metadata
        self._last_agent = agent
        self._last_context = context

        self._model_config = RealtimeModelConfig(
            api_key=settings.openai_api_key,
            initial_model_settings=RealtimeSessionModelSettings(
                model_name=cfg.realtime_model,
                reasoning=RealtimeReasoningConfig(effort=cfg.reasoning_effort),
                voice=cfg.voice,
                speed=cfg.voice_speed,
                input_audio_format=cfg.audio_format,
                output_audio_format=cfg.audio_format,
                input_audio_transcription=RealtimeInputAudioTranscriptionConfig(
                    model=cfg.transcription_model,
                    language="en",
                ),
                input_audio_noise_reduction=RealtimeInputAudioNoiseReductionConfig(
                    type=cfg.input_audio_noise_reduction,
                ),
                turn_detection=RealtimeTurnDetectionConfig(
                    type=cfg.turn_detection_type,
                    eagerness=cfg.turn_detection_eagerness,
                    interrupt_response=cfg.turn_detection_interrupt_response,
                    create_response=cfg.turn_detection_create_response,
                ),
                tracing=RealtimeModelTracingConfig(
                    workflow_name="Resident One Voice",
                    group_id=self.group_id,
                ),
            ),
            playback_tracker=self.playback_tracker,
        )

        # Apply custom WSS endpoint if configured
        if cfg.openai_base_wss_url:
            self._model_config["url"] = f"{cfg.openai_base_wss_url}?model={cfg.realtime_model}"

        # Attach tracing metadata
        try:
            tracing_metadata = {k: str(v) for k, v in metadata.items() if v is not None}
            self._model_config["initial_model_settings"]["tracing"]["metadata"] = tracing_metadata
        except (TypeError, KeyError):
            pass

        runner = RealtimeRunner(agent)
        self.session = await runner.run(
            context=context,
            model_config=self._model_config,
        )

    async def enter(self, max_retries: int = 2) -> None:
        """Enter the session (starts the WebSocket), with retry on transient failures.

        Retries handle transient OpenAI WebSocket errors (e.g. 1011 internal error)
        that can kill a call before it starts.  On failure the session is torn down
        and rebuilt from the stored ``create()`` parameters before the next attempt.
        """
        last_exc: Exception | None = None
        for attempt in range(1, max_retries + 1):
            try:
                await self.session.enter()
                self._session_ready.set()
                return
            except Exception as exc:
                last_exc = exc
                logger.warning(f"Session enter failed (attempt {attempt}/{max_retries}): {exc}")
                if attempt < max_retries:
                    if self.session:
                        try:
                            await self.session.close()
                        except Exception as close_err:
                            logger.debug(f"Error closing failed session: {close_err}")
                        finally:
                            self.session = None
                    await asyncio.sleep(0.5 * attempt)
                    # Rebuild the session from stored parameters
                    await self.create(
                        agent=self._last_agent,
                        context=self._last_context,
                        metadata=self._metadata,
                    )
        raise last_exc  # type: ignore[misc]

    # ------------------------------------------------------------------
    # Event stream
    # ------------------------------------------------------------------

    async def events(self) -> AsyncIterator[RealtimeSessionEvent]:
        """Yield events from the session. Caller iterates with ``async for``."""
        await self._session_ready.wait()
        assert self.session is not None
        async for event in self.session:
            yield event

    # ------------------------------------------------------------------
    # Sending to the session
    # ------------------------------------------------------------------

    async def send_audio(self, audio: bytes) -> None:
        """Send buffered input audio to the OpenAI session."""
        if self.session:
            await self.session.send_audio(audio)

    async def send_message(self, message: str) -> None:
        """Send a text message to the session (filler, guardrail, recovery)."""
        if self.session:
            await self.session.send_message(message)

    async def send_event(self, event: Any) -> None:
        """Send a raw event via the session's internal model.

        ``RealtimeSession`` does not expose ``send_event`` directly —
        it lives on ``session._model``.  This is how the original
        twilio_handler sends ``RealtimeModelSendRawMessage`` and
        ``RealtimeModelSendInterrupt``.
        """
        if self.session and hasattr(self.session, "_model"):
            await self.session._model.send_event(event)

    async def send_interrupt(self) -> None:
        """Send an interrupt to cancel the current response."""
        if self.session:
            await self.session.interrupt()

    async def cancel_response(self) -> None:
        """Cancel any active response via the session."""
        await self.send_interrupt()

    async def force_cancel_response(self) -> None:
        """Force-cancel the in-flight response.

        With ``turn_detection.interrupt_response=True`` the SDK skips
        ``response.cancel`` on barge-in, but in race conditions (e.g.
        guardrails or filler messages overlapping a user turn) the model
        emits ``"already has an active response in progress"`` on the next
        ``response.create`` and we need an explicit cancel.  See
        https://github.com/openai/openai-agents-python/issues/1907.
        """
        await self.send_event(RealtimeModelSendInterrupt(force_response_cancel=True))

    def is_response_active(self) -> bool:
        """Return whether the SDK currently has an in-flight response."""
        if self.session and hasattr(self.session, "_model"):
            return bool(getattr(self.session._model, "_ongoing_response", False))
        return False

    async def create_response(
        self,
        instructions: str | None = None,
        output_modalities: list[str] | None = None,
    ) -> None:
        """Trigger a new response from the model.

        The ``response`` config must be wrapped in ``other_data`` so the
        SDK's ``try_convert_raw_message`` forwards it.  The field is
        ``output_modalities`` (GA schema), not ``modalities`` (legacy beta).
        See PR #1300 / KNCK-38774.

        Args:
            instructions: Optional per-response instructions.
            output_modalities: Optional modalities list (e.g. ``["audio"]``).
        """
        msg: dict[str, Any] = {"type": "response.create"}
        response_config: dict[str, Any] = {}
        if instructions:
            response_config["instructions"] = instructions
        if output_modalities:
            response_config["output_modalities"] = output_modalities
        if response_config:
            msg["other_data"] = {"response": response_config}
        await self.send_event(RealtimeModelSendRawMessage(message=msg))

    async def update_agent(self, agent: Any) -> None:
        """Update the session's agent (e.g. after greeting completes)."""
        if self.session:
            await self.session.update_agent(agent)

    # ------------------------------------------------------------------
    # History
    # ------------------------------------------------------------------

    def on_history_updated(self, new_history: list[Any]) -> None:
        """Update the conversation history from a session event."""
        self.history = new_history

    def get_input_list(self) -> list[Any]:
        """Convert history to the input format expected by the thinker."""
        return realtime_history_to_input_list(self.history)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Close the session and release resources."""
        if self.session:
            try:
                await self.session.close()
            except Exception as e:
                logger.debug(f"Error closing session: {e}")
            finally:
                self.session = None
                self._session_ready.clear()
