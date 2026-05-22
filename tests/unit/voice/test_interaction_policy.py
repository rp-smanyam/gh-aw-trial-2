"""Tests for InteractionPolicy — greeting, ESR, default policies."""

import pytest

from agent_leasing.voice.coordination.call_state import VoiceCallState
from agent_leasing.voice.coordination.interaction_policy import (
    DefaultPolicy,
    ESRPolicy,
    GreetingPolicy,
)


class TestDefaultPolicy:
    def test_does_not_suppress_interrupt(self):
        cs = VoiceCallState()
        p = DefaultPolicy()
        assert p.should_suppress_interrupt(cs) is False

    def test_accepts_audio(self):
        cs = VoiceCallState()
        p = DefaultPolicy()
        assert p.should_accept_audio(cs) is True

    @pytest.mark.asyncio
    async def test_on_playback_complete_returns_self(self):
        cs = VoiceCallState()
        p = DefaultPolicy()
        result = await p.on_playback_complete(cs)
        assert result is p


class TestGreetingPolicy:
    def test_suppresses_interrupt(self):
        cs = VoiceCallState()
        p = GreetingPolicy()
        assert p.should_suppress_interrupt(cs) is True

    def test_accepts_audio(self):
        cs = VoiceCallState()
        p = GreetingPolicy()
        assert p.should_accept_audio(cs) is True

    @pytest.mark.asyncio
    async def test_transitions_to_default_on_playback_complete(self):
        cs = VoiceCallState()
        p = GreetingPolicy()
        result = await p.on_playback_complete(cs)
        assert isinstance(result, DefaultPolicy)


class TestESRPolicy:
    def test_suppresses_interrupt(self):
        cs = VoiceCallState()
        p = ESRPolicy()
        assert p.should_suppress_interrupt(cs) is True

    @pytest.mark.asyncio
    async def test_transitions_to_default_on_playback_complete(self):
        cs = VoiceCallState()
        p = ESRPolicy()
        result = await p.on_playback_complete(cs)
        assert isinstance(result, DefaultPolicy)
