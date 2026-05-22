import asyncio
from contextlib import contextmanager
from unittest import mock

from agents.realtime import (
    AssistantMessageItem,
    InputAudio,
    RealtimeToolCallItem,
    UserMessageItem,
)
from agents.realtime.items import AssistantAudio

from agent_leasing.api.model import Author, Flow
from agent_leasing.util.realtime_util import (
    log_data_curation_event_for_realtime_events,
    realtime_history_to_input_item,
    realtime_history_to_input_list,
)


class TestRealtimeHistoryToInputList:
    def test_realtime_history_to_input_list_without_item_id(self):
        """Test default behavior (for thinker agents) - no item_id included."""
        history = [
            UserMessageItem(
                item_id="item_CGSEuhpnsRzqJKk3605i4",
                previous_item_id="",
                type="message",
                role="user",
                content=[
                    InputAudio(
                        type="input_audio",
                        audio=None,
                        transcript="Hello.",
                    )
                ],
                status="completed",
            ),
            AssistantMessageItem(
                item_id="item_CGSEv4pMlhOUphsaMnUwH",
                previous_item_id=None,
                type="message",
                role="assistant",
                status="in_progress",
                content=[
                    AssistantAudio(
                        type="audio",
                        audio=None,
                        transcript="Hi there! How can I assist you today?",
                    )
                ],
            ),
            UserMessageItem(
                item_id="item_CGSFDtcoOeXsiYNzEymKY",
                previous_item_id="item_CGSEv4pMlhOUphsaMnUwH",
                type="message",
                role="user",
                content=[
                    InputAudio(
                        type="input_audio",
                        audio=None,
                        transcript="Oh, interesting.",
                    )
                ],
                status="completed",
            ),
            AssistantMessageItem(
                item_id="item_CGSFGw41Rg21bIMyVYHZE",
                previous_item_id="item_CGSFDtcoOeXsiYNzEymKY",
                type="message",
                role="assistant",
                status="in_progress",
                content=[],
            ),
        ]
        # Default behavior - no item_id (for thinker agents)
        assert realtime_history_to_input_list(history) == [
            {
                "content": "Hello.",
                "role": "user",
            },
            {
                "content": "Hi there! How can I assist you today?",
                "role": "assistant",
            },
            {
                "content": "Oh, interesting.",
                "role": "user",
            },
        ]

    def test_realtime_history_to_input_list_with_item_id(self):
        """Test with item_id included (for realtime/logging scenarios)."""
        history = [
            UserMessageItem(
                item_id="item_CGSEuhpnsRzqJKk3605i4",
                previous_item_id="",
                type="message",
                role="user",
                content=[
                    InputAudio(
                        type="input_audio",
                        audio=None,
                        transcript="Hello.",
                    )
                ],
                status="completed",
            ),
            AssistantMessageItem(
                item_id="item_CGSEv4pMlhOUphsaMnUwH",
                previous_item_id=None,
                type="message",
                role="assistant",
                status="in_progress",
                content=[
                    AssistantAudio(
                        type="audio",
                        audio=None,
                        transcript="Hi there! How can I assist you today?",
                    )
                ],
            ),
        ]
        # With item_id included (for realtime/logging scenarios)
        assert realtime_history_to_input_list(history, include_item_id=True) == [
            {
                "item_id": "item_CGSEuhpnsRzqJKk3605i4",
                "content": "Hello.",
                "role": "user",
            },
            {
                "item_id": "item_CGSEv4pMlhOUphsaMnUwH",
                "content": "Hi there! How can I assist you today?",
                "role": "assistant",
            },
        ]

    def test_realtime_history_to_input_list_missing_field(self):
        """Test filtering out items with missing transcript fields."""
        history = [
            UserMessageItem(
                item_id="item_CGSEuhpnsRzqJKk3605i4",
                previous_item_id="",
                type="message",
                role="user",
                content=[
                    InputAudio(
                        type="input_audio",
                        audio=None,
                        transcript=None,  # Missing transcript - should be filtered out
                    )
                ],
                status="completed",
            ),
            AssistantMessageItem(
                item_id="item_CGSEv4pMlhOUphsaMnUwH",
                previous_item_id=None,
                type="message",
                role="assistant",
                status="in_progress",
                content=[
                    AssistantAudio(
                        type="audio",
                        audio=None,
                        transcript="Hi there! How can I assist you today?",
                    )
                ],
            ),
        ]
        # Default behavior - no item_id, and missing transcript is filtered out
        assert realtime_history_to_input_list(history) == [
            {
                "content": "Hi there! How can I assist you today?",
                "role": "assistant",
            },
        ]


class TestLogDataCurationEventForRealtimeEvents:
    @mock.patch("agent_leasing.util.realtime_util.classify_language")
    @mock.patch("agent_leasing.util.realtime_util.log_data_curation_event")
    async def test_log_data_curation_event_for_realtime_events(
        self,
        mock_log_data_curation_event,
        mock_classify_language,
        resident_context_voice_knck,
    ):
        # Setup mock to return a language classification result
        mock_language_result = mock.Mock()
        mock_language_result.language_code = "en"
        mock_classify_language.return_value = mock_language_result

        session = mock.Mock()
        session._context_wrapper = mock.Mock()
        session._context_wrapper.context = resident_context_voice_knck

        session._history = [
            UserMessageItem(
                item_id="user-message-item-id",
                content=[InputAudio(transcript="Hey there!")],
            ),
            AssistantMessageItem(
                item_id="assistant-message-item-id",
                content=[AssistantAudio(transcript="Hey there! How can I assist you today?")],
            ),
            UserMessageItem(
                item_id="user-message-item-id2",
                content=[InputAudio(transcript="Are there any upcoming community events?")],
            ),
            AssistantMessageItem(
                item_id="assistant-message-item-id2",
                content=[AssistantAudio(transcript="Let me look that up for you.")],
            ),
            RealtimeToolCallItem(
                item_id="realtime-tool-call-item-id",
                call_id="call-id",
                status="completed",
                arguments='{  \n  "input": "find upcoming community events for resident"  \n}  \n',
                name="community_thinker_tool",
            ),
            AssistantMessageItem(
                item_id="assistant-message-item-id3",
                content=[
                    AssistantAudio(
                        transcript="Here are two upcoming events: the Sunset Social "
                        "Mixer on October sixth from nine PM to eleven PM, and the Tech "
                        "& Tea Social on November first from two PM to four PM. Would you"
                        " like to sign up for any of these, or do you need more details?",
                    ),
                ],
            ),
            UserMessageItem(
                item_id="user-message-item-id3",
                content=[InputAudio(transcript="Thanks, I'm good.")],
            ),
            AssistantMessageItem(
                item_id="assistant-message-item-id4",
                content=[AssistantAudio(transcript="Alright, no problem at all. How else can I assist you today?")],
            ),
            UserMessageItem(
                item_id="user-message-item-id4",
                content=[InputAudio(transcript="Can you connect me to a human agent?")],
            ),
            AssistantMessageItem(
                item_id="assistant-message-item-id5",
                content=[
                    AssistantAudio(
                        transcript="I'll be happy to connect you to a staff member to assist you. "
                        "Can you provide me a summary of the issue so I can connect you to the right person?",
                    )
                ],
            ),
            UserMessageItem(
                item_id="user-message-item-id4",
                content=[InputAudio(transcript="A problem about a package.")],
            ),
            AssistantMessageItem(
                item_id="assistant-message-item-id5",
                content=[
                    AssistantAudio(
                        transcript="Thanks for letting me know. I'll connect you to a staff member to assist "
                        "you with the package problem. Please standby while I connect you.",
                    )
                ],
            ),
            RealtimeToolCallItem(
                item_id="realtime-tool-call-item-id2",
                call_id="call-id2",
                status="completed",
                arguments='{  \n  "transfer_message": "Connecting resident '
                'to staff for assistance with a package problem.",  \n  '
                '"tool_use_reason": "Resident confirmed request to speak to a '
                'staff member for assistance with a package problem.",  \n  '
                '"user_confirmation": true \n}  \n',
                name="transfer_to_staff_voice",
            ),
        ]

        workflow_name = resident_context_voice_knck.ask_request.product.upper()
        default_flows = [Flow(name=workflow_name)]
        community_flows = [Flow(name="community_thinker_tool")]
        transfer_to_staff_voice_flows = [Flow(name="transfer_to_staff_voice")]
        end_flows = [Flow(name="END")]

        await log_data_curation_event_for_realtime_events(session)

        assert mock_log_data_curation_event.call_args_list[0][1] == {
            "chat_session_id": resident_context_voice_knck.ask_request.chat_session_id,
            "conversation_type": resident_context_voice_knck.ask_request.conversation_type,
            "body": "Hey there!",
            "call_sid": resident_context_voice_knck.ask_request.product_info.call_sid,
            "property_id": resident_context_voice_knck.property_id,
            "applicant_id": resident_context_voice_knck.ask_request.resident_id,
            "bot_type": resident_context_voice_knck.persona,
            "author": Author.CONTACT,
            "flows": default_flows,
            "language": "en",
            "metadata": [],
            "openai_trace_url": None,
            "langsmith_trace_url": None,
        }

        assert mock_log_data_curation_event.call_args_list[1][1] == {
            "chat_session_id": resident_context_voice_knck.ask_request.chat_session_id,
            "conversation_type": resident_context_voice_knck.ask_request.conversation_type,
            "body": "Hey there! How can I assist you today?",
            "call_sid": resident_context_voice_knck.ask_request.product_info.call_sid,
            "property_id": resident_context_voice_knck.property_id,
            "applicant_id": resident_context_voice_knck.ask_request.resident_id,
            "bot_type": resident_context_voice_knck.persona,
            "author": Author.BOT,
            "flows": default_flows,
            "language": "en",
            "metadata": [],
            "openai_trace_url": None,
            "langsmith_trace_url": None,
        }

        assert mock_log_data_curation_event.call_args_list[2][1] == {
            "chat_session_id": resident_context_voice_knck.ask_request.chat_session_id,
            "conversation_type": resident_context_voice_knck.ask_request.conversation_type,
            "body": "Are there any upcoming community events?",
            "call_sid": resident_context_voice_knck.ask_request.product_info.call_sid,
            "property_id": resident_context_voice_knck.property_id,
            "applicant_id": resident_context_voice_knck.ask_request.resident_id,
            "bot_type": resident_context_voice_knck.persona,
            "author": Author.CONTACT,
            "flows": default_flows,
            "language": "en",
            "metadata": [],
            "openai_trace_url": None,
            "langsmith_trace_url": None,
        }

        assert mock_log_data_curation_event.call_args_list[3][1] == {
            "chat_session_id": resident_context_voice_knck.ask_request.chat_session_id,
            "conversation_type": resident_context_voice_knck.ask_request.conversation_type,
            "body": "Let me look that up for you.",
            "call_sid": resident_context_voice_knck.ask_request.product_info.call_sid,
            "property_id": resident_context_voice_knck.property_id,
            "applicant_id": resident_context_voice_knck.ask_request.resident_id,
            "bot_type": resident_context_voice_knck.persona,
            "author": Author.BOT,
            "flows": default_flows,
            "language": "en",
            "metadata": [],
            "openai_trace_url": None,
            "langsmith_trace_url": None,
        }

        assert mock_log_data_curation_event.call_args_list[4][1] == {
            "chat_session_id": resident_context_voice_knck.ask_request.chat_session_id,
            "conversation_type": resident_context_voice_knck.ask_request.conversation_type,
            "body": "Here are two upcoming events: the Sunset Social "
            "Mixer on October sixth from nine PM to eleven PM, and the Tech "
            "& Tea Social on November first from two PM to four PM. Would you"
            " like to sign up for any of these, or do you need more details?",
            "call_sid": resident_context_voice_knck.ask_request.product_info.call_sid,
            "property_id": resident_context_voice_knck.property_id,
            "applicant_id": resident_context_voice_knck.ask_request.resident_id,
            "bot_type": resident_context_voice_knck.persona,
            "author": Author.BOT,
            "flows": community_flows,
            "language": "en",
            "metadata": [],
            "openai_trace_url": None,
            "langsmith_trace_url": None,
        }

        assert mock_log_data_curation_event.call_args_list[5][1] == {
            "chat_session_id": resident_context_voice_knck.ask_request.chat_session_id,
            "conversation_type": resident_context_voice_knck.ask_request.conversation_type,
            "body": "Thanks, I'm good.",
            "call_sid": resident_context_voice_knck.ask_request.product_info.call_sid,
            "property_id": resident_context_voice_knck.property_id,
            "applicant_id": resident_context_voice_knck.ask_request.resident_id,
            "bot_type": resident_context_voice_knck.persona,
            "author": Author.CONTACT,
            "flows": default_flows,
            "language": "en",
            "metadata": [],
            "openai_trace_url": None,
            "langsmith_trace_url": None,
        }

        assert mock_log_data_curation_event.call_args_list[6][1] == {
            "chat_session_id": resident_context_voice_knck.ask_request.chat_session_id,
            "conversation_type": resident_context_voice_knck.ask_request.conversation_type,
            "body": "Alright, no problem at all. How else can I assist you today?",
            "call_sid": resident_context_voice_knck.ask_request.product_info.call_sid,
            "property_id": resident_context_voice_knck.property_id,
            "applicant_id": resident_context_voice_knck.ask_request.resident_id,
            "bot_type": resident_context_voice_knck.persona,
            "author": Author.BOT,
            "flows": default_flows,
            "language": "en",
            "metadata": [],
            "openai_trace_url": None,
            "langsmith_trace_url": None,
        }

        assert mock_log_data_curation_event.call_args_list[7][1] == {
            "chat_session_id": resident_context_voice_knck.ask_request.chat_session_id,
            "conversation_type": resident_context_voice_knck.ask_request.conversation_type,
            "body": "Can you connect me to a human agent?",
            "call_sid": resident_context_voice_knck.ask_request.product_info.call_sid,
            "property_id": resident_context_voice_knck.property_id,
            "applicant_id": resident_context_voice_knck.ask_request.resident_id,
            "bot_type": resident_context_voice_knck.persona,
            "author": Author.CONTACT,
            "flows": default_flows,
            "language": "en",
            "metadata": [],
            "openai_trace_url": None,
            "langsmith_trace_url": None,
        }

        assert mock_log_data_curation_event.call_args_list[8][1] == {
            "chat_session_id": resident_context_voice_knck.ask_request.chat_session_id,
            "conversation_type": resident_context_voice_knck.ask_request.conversation_type,
            "body": (
                "I'll be happy to connect you to a staff member to assist you. "
                "Can you provide me a summary of the issue so I can connect you to the right person?"
            ),
            "call_sid": resident_context_voice_knck.ask_request.product_info.call_sid,
            "property_id": resident_context_voice_knck.property_id,
            "applicant_id": resident_context_voice_knck.ask_request.resident_id,
            "bot_type": resident_context_voice_knck.persona,
            "author": Author.BOT,
            "flows": default_flows,
            "language": "en",
            "metadata": [],
            "openai_trace_url": None,
            "langsmith_trace_url": None,
        }

        assert mock_log_data_curation_event.call_args_list[9][1] == {
            "chat_session_id": resident_context_voice_knck.ask_request.chat_session_id,
            "conversation_type": resident_context_voice_knck.ask_request.conversation_type,
            "body": "A problem about a package.",
            "call_sid": resident_context_voice_knck.ask_request.product_info.call_sid,
            "property_id": resident_context_voice_knck.property_id,
            "applicant_id": resident_context_voice_knck.ask_request.resident_id,
            "bot_type": resident_context_voice_knck.persona,
            "author": Author.CONTACT,
            "flows": default_flows,
            "language": "en",
            "metadata": [],
            "openai_trace_url": None,
            "langsmith_trace_url": None,
        }

        assert mock_log_data_curation_event.call_args_list[10][1] == {
            "chat_session_id": resident_context_voice_knck.ask_request.chat_session_id,
            "conversation_type": resident_context_voice_knck.ask_request.conversation_type,
            "body": (
                "Thanks for letting me know. I'll connect you to a staff member to assist "
                "you with the package problem. Please standby while I connect you."
            ),
            "call_sid": resident_context_voice_knck.ask_request.product_info.call_sid,
            "property_id": resident_context_voice_knck.property_id,
            "applicant_id": resident_context_voice_knck.ask_request.resident_id,
            "bot_type": resident_context_voice_knck.persona,
            "author": Author.BOT,
            "flows": transfer_to_staff_voice_flows,
            "language": "en",
            "metadata": [],
            "openai_trace_url": None,
            "langsmith_trace_url": None,
        }

        assert mock_log_data_curation_event.call_args_list[11][1] == {
            "chat_session_id": resident_context_voice_knck.ask_request.chat_session_id,
            "conversation_type": resident_context_voice_knck.ask_request.conversation_type,
            "body": "END",
            "call_sid": resident_context_voice_knck.ask_request.product_info.call_sid,
            "property_id": resident_context_voice_knck.property_id,
            "applicant_id": resident_context_voice_knck.ask_request.resident_id,
            "bot_type": resident_context_voice_knck.persona,
            "author": Author.BOT,
            "flows": end_flows,
            "language": "en",
            "openai_trace_url": None,
            "langsmith_trace_url": None,
        }

        # Check the classify_language calls
        expected_classify_calls = [
            mock.call("Hey there!"),
            mock.call("Hey there! How can I assist you today?"),
            mock.call("Are there any upcoming community events?"),
            mock.call("Let me look that up for you."),
            mock.call(
                "Here are two upcoming events: the Sunset Social Mixer on October sixth from nine PM to eleven PM, and the Tech & Tea Social on November first from two PM to four PM. Would you like to sign up for any of these, or do you need more details?"
            ),
            mock.call("Thanks, I'm good."),
            mock.call("Alright, no problem at all. How else can I assist you today?"),
            mock.call("Can you connect me to a human agent?"),
            mock.call(
                "I'll be happy to connect you to a staff member to assist you. "
                "Can you provide me a summary of the issue so I can connect you to the right person?"
            ),
            mock.call("A problem about a package."),
            mock.call(
                "Thanks for letting me know. I'll connect you to a staff member to assist "
                "you with the package problem. Please standby while I connect you."
            ),
            # skipped the "END" call
        ]
        mock_classify_language.assert_has_calls(expected_classify_calls)

    @mock.patch("agent_leasing.util.realtime_util.log_data_curation_event")
    async def test_language_classification_runs_inside_trace(
        self,
        _mock_log_data_curation_event,
        resident_context_voice_knck,
    ):
        trace_active = False

        @contextmanager
        def fake_trace(*_args, **_kwargs):
            nonlocal trace_active
            trace_active = True
            try:
                yield
            finally:
                trace_active = False

        async def _classify_language(text):
            assert trace_active is True
            result = mock.Mock()
            result.language_code = "en"
            return result

        session = mock.Mock()
        session._context_wrapper = mock.Mock()
        session._context_wrapper.context = resident_context_voice_knck
        session._history = [
            UserMessageItem(
                item_id="user-message-item-id",
                content=[InputAudio(transcript="Hey there!")],
            ),
        ]

        with mock.patch("agent_leasing.util.realtime_util.trace", fake_trace):
            with mock.patch(
                "agent_leasing.util.realtime_util.classify_language",
                side_effect=_classify_language,
            ) as mock_classify_language:
                await log_data_curation_event_for_realtime_events(session)
                assert mock_classify_language.called

    @mock.patch("agent_leasing.util.realtime_util.log_data_curation_event")
    async def test_language_classification_runs_inside_single_langsmith_span(
        self,
        _mock_log_data_curation_event,
        resident_context_voice_knck,
    ):
        langsmith_span_active = False
        langsmith_run = mock.Mock()

        @contextmanager
        def fake_langsmith_trace(*_args, **_kwargs):
            nonlocal langsmith_span_active
            langsmith_span_active = True
            try:
                yield langsmith_run
            finally:
                langsmith_span_active = False

        async def _classify_language(_text):
            assert langsmith_span_active is True
            result = mock.Mock()
            result.language_code = "en"
            return result

        session = mock.Mock()
        session._context_wrapper = mock.Mock()
        session._context_wrapper.context = resident_context_voice_knck
        session._history = [
            UserMessageItem(
                item_id="user-message-item-id",
                content=[InputAudio(transcript="Hey there!")],
            ),
            AssistantMessageItem(
                item_id="assistant-message-item-id",
                content=[AssistantAudio(transcript="Hello, how can I help?")],
            ),
        ]

        with mock.patch(
            "agent_leasing.util.realtime_util.ls.trace",
            side_effect=fake_langsmith_trace,
        ) as mock_langsmith_trace:
            with mock.patch(
                "agent_leasing.util.realtime_util.classify_language",
                side_effect=_classify_language,
            ) as mock_classify_language:
                await log_data_curation_event_for_realtime_events(session)

        assert mock_classify_language.call_count == 2
        mock_langsmith_trace.assert_called_once()
        assert mock_langsmith_trace.call_args.kwargs["name"] == "language_classification"
        assert mock_langsmith_trace.call_args.kwargs["run_type"] == "chain"
        assert mock_langsmith_trace.call_args.kwargs["inputs"]["message_count"] == 2
        langsmith_run.end.assert_called_once_with(outputs={"language_codes": ["en", "en"]})

    @mock.patch("agent_leasing.util.realtime_util.classify_language")
    @mock.patch("agent_leasing.util.realtime_util.log_data_curation_event")
    async def test_log_data_curation_event_for_realtime_events_when_have_multiple_tool_calls(
        self,
        mock_log_data_curation_event,
        mock_classify_language,
        resident_context_voice_knck,
    ):
        resident_context_voice_knck.logging_metadata = {
            "create service request for resident": {
                "service_request": [
                    "create_service_request",
                    {"created": True, "sr_id": 53362},
                ]
            },
        }
        # Setup mock to return a language classification result
        mock_language_result = mock.Mock()
        mock_language_result.language_code = "en"
        mock_classify_language.return_value = mock_language_result

        session = mock.Mock()
        session._context_wrapper = mock.Mock()
        session._context_wrapper.context = resident_context_voice_knck

        session._history = [
            UserMessageItem(
                item_id="user-message-item-id",
                content=[InputAudio(transcript="Hey there!")],
            ),
            AssistantMessageItem(
                item_id="assistant-message-item-id",
                content=[AssistantAudio(transcript="Hey there! How can I assist you today?")],
            ),
            UserMessageItem(
                item_id="user-message-item-id2",
                content=[InputAudio(transcript="Create me a service request for my leaking tap in the kitchen.")],
            ),
            AssistantMessageItem(
                item_id="assistant-message-item-id2",
                content=[AssistantAudio(transcript="Let me look that up for you.")],
            ),
            RealtimeToolCallItem(
                item_id="realtime-tool-call-item-id",
                call_id="call-id",
                status="completed",
                arguments='{  \n  "input": "create service request for resident"  \n}  \n',
                name="facilities_thinker_tool",
            ),
            AssistantMessageItem(
                item_id="assistant-message-item-id3",
                content=[
                    AssistantAudio(
                        transcript="I've created service request for you. Do you want me to send a text?",
                    ),
                ],
            ),
            UserMessageItem(
                item_id="user-message-item-id3",
                content=[InputAudio(transcript="Thanks, I'm good.")],
            ),
            AssistantMessageItem(
                item_id="assistant-message-item-id4",
                content=[AssistantAudio(transcript="Alright, no problem at all. How else can I assist you today?")],
            ),
            UserMessageItem(
                item_id="user-message-item-id4",
                content=[InputAudio(transcript="Can you connect me to a human agent?")],
            ),
            AssistantMessageItem(
                item_id="assistant-message-item-id5",
                content=[
                    AssistantAudio(
                        transcript="I'll be happy to connect you to a staff member to assist you. "
                        "Can you provide me a summary of the issue so I can connect you to the right person?",
                    )
                ],
            ),
            UserMessageItem(
                item_id="user-message-item-id4",
                content=[InputAudio(transcript="A problem about a package.")],
            ),
            AssistantMessageItem(
                item_id="assistant-message-item-id5",
                content=[
                    AssistantAudio(
                        transcript="Thanks for letting me know. I'll connect you to a staff member to assist "
                        "you with the package problem. Please standby while I connect you.",
                    )
                ],
            ),
            RealtimeToolCallItem(
                item_id="realtime-tool-call-item-id2",
                call_id="call-id2",
                status="completed",
                arguments='{  \n  "transfer_message": "Connecting resident '
                'to staff for assistance with a package problem.",  \n  '
                '"tool_use_reason": "Resident confirmed request to speak to a '
                'staff member for assistance with a package problem.",  \n  '
                '"user_confirmation": true \n}  \n',
                name="transfer_to_staff_voice",
            ),
        ]

        workflow_name = resident_context_voice_knck.ask_request.product.upper()
        default_flows = [Flow(name=workflow_name)]
        facilities_flows = [Flow(name="facilities_thinker_tool")]
        transfer_to_staff_voice_flows = [Flow(name="transfer_to_staff_voice")]
        end_flows = [Flow(name="END")]

        await log_data_curation_event_for_realtime_events(session)

        assert mock_log_data_curation_event.call_args_list[0][1] == {
            "chat_session_id": resident_context_voice_knck.ask_request.chat_session_id,
            "conversation_type": resident_context_voice_knck.ask_request.conversation_type,
            "body": "Hey there!",
            "call_sid": resident_context_voice_knck.ask_request.product_info.call_sid,
            "property_id": resident_context_voice_knck.property_id,
            "applicant_id": resident_context_voice_knck.ask_request.resident_id,
            "bot_type": resident_context_voice_knck.persona,
            "author": Author.CONTACT,
            "flows": default_flows,
            "language": "en",
            "metadata": [],
            "openai_trace_url": None,
            "langsmith_trace_url": None,
        }

        assert mock_log_data_curation_event.call_args_list[1][1] == {
            "chat_session_id": resident_context_voice_knck.ask_request.chat_session_id,
            "conversation_type": resident_context_voice_knck.ask_request.conversation_type,
            "body": "Hey there! How can I assist you today?",
            "call_sid": resident_context_voice_knck.ask_request.product_info.call_sid,
            "property_id": resident_context_voice_knck.property_id,
            "applicant_id": resident_context_voice_knck.ask_request.resident_id,
            "bot_type": resident_context_voice_knck.persona,
            "author": Author.BOT,
            "flows": default_flows,
            "language": "en",
            "metadata": [],
            "openai_trace_url": None,
            "langsmith_trace_url": None,
        }

        assert mock_log_data_curation_event.call_args_list[2][1] == {
            "chat_session_id": resident_context_voice_knck.ask_request.chat_session_id,
            "conversation_type": resident_context_voice_knck.ask_request.conversation_type,
            "body": "Create me a service request for my leaking tap in the kitchen.",
            "call_sid": resident_context_voice_knck.ask_request.product_info.call_sid,
            "property_id": resident_context_voice_knck.property_id,
            "applicant_id": resident_context_voice_knck.ask_request.resident_id,
            "bot_type": resident_context_voice_knck.persona,
            "author": Author.CONTACT,
            "flows": default_flows,
            "language": "en",
            "metadata": [],
            "openai_trace_url": None,
            "langsmith_trace_url": None,
        }

        assert mock_log_data_curation_event.call_args_list[3][1] == {
            "chat_session_id": resident_context_voice_knck.ask_request.chat_session_id,
            "conversation_type": resident_context_voice_knck.ask_request.conversation_type,
            "body": "Let me look that up for you.",
            "call_sid": resident_context_voice_knck.ask_request.product_info.call_sid,
            "property_id": resident_context_voice_knck.property_id,
            "applicant_id": resident_context_voice_knck.ask_request.resident_id,
            "bot_type": resident_context_voice_knck.persona,
            "author": Author.BOT,
            "flows": default_flows,
            "language": "en",
            "metadata": [],
            "openai_trace_url": None,
            "langsmith_trace_url": None,
        }

        assert mock_log_data_curation_event.call_args_list[4][1] == {
            "chat_session_id": resident_context_voice_knck.ask_request.chat_session_id,
            "conversation_type": resident_context_voice_knck.ask_request.conversation_type,
            "body": "I've created service request for you. Do you want me to send a text?",
            "call_sid": resident_context_voice_knck.ask_request.product_info.call_sid,
            "property_id": resident_context_voice_knck.property_id,
            "applicant_id": resident_context_voice_knck.ask_request.resident_id,
            "bot_type": resident_context_voice_knck.persona,
            "author": Author.BOT,
            "flows": facilities_flows,
            "language": "en",
            "metadata": [*resident_context_voice_knck.logging_metadata.values()],
            "openai_trace_url": None,
            "langsmith_trace_url": None,
        }

        assert mock_log_data_curation_event.call_args_list[5][1] == {
            "chat_session_id": resident_context_voice_knck.ask_request.chat_session_id,
            "conversation_type": resident_context_voice_knck.ask_request.conversation_type,
            "body": "Thanks, I'm good.",
            "call_sid": resident_context_voice_knck.ask_request.product_info.call_sid,
            "property_id": resident_context_voice_knck.property_id,
            "applicant_id": resident_context_voice_knck.ask_request.resident_id,
            "bot_type": resident_context_voice_knck.persona,
            "author": Author.CONTACT,
            "flows": default_flows,
            "language": "en",
            "metadata": [],
            "openai_trace_url": None,
            "langsmith_trace_url": None,
        }

        assert mock_log_data_curation_event.call_args_list[6][1] == {
            "chat_session_id": resident_context_voice_knck.ask_request.chat_session_id,
            "conversation_type": resident_context_voice_knck.ask_request.conversation_type,
            "body": "Alright, no problem at all. How else can I assist you today?",
            "call_sid": resident_context_voice_knck.ask_request.product_info.call_sid,
            "property_id": resident_context_voice_knck.property_id,
            "applicant_id": resident_context_voice_knck.ask_request.resident_id,
            "bot_type": resident_context_voice_knck.persona,
            "author": Author.BOT,
            "flows": default_flows,
            "language": "en",
            "metadata": [],
            "openai_trace_url": None,
            "langsmith_trace_url": None,
        }

        assert mock_log_data_curation_event.call_args_list[7][1] == {
            "chat_session_id": resident_context_voice_knck.ask_request.chat_session_id,
            "conversation_type": resident_context_voice_knck.ask_request.conversation_type,
            "body": "Can you connect me to a human agent?",
            "call_sid": resident_context_voice_knck.ask_request.product_info.call_sid,
            "property_id": resident_context_voice_knck.property_id,
            "applicant_id": resident_context_voice_knck.ask_request.resident_id,
            "bot_type": resident_context_voice_knck.persona,
            "author": Author.CONTACT,
            "flows": default_flows,
            "language": "en",
            "metadata": [],
            "openai_trace_url": None,
            "langsmith_trace_url": None,
        }

        assert mock_log_data_curation_event.call_args_list[8][1] == {
            "chat_session_id": resident_context_voice_knck.ask_request.chat_session_id,
            "conversation_type": resident_context_voice_knck.ask_request.conversation_type,
            "body": (
                "I'll be happy to connect you to a staff member to assist you. "
                "Can you provide me a summary of the issue so I can connect you to the right person?"
            ),
            "call_sid": resident_context_voice_knck.ask_request.product_info.call_sid,
            "property_id": resident_context_voice_knck.property_id,
            "applicant_id": resident_context_voice_knck.ask_request.resident_id,
            "bot_type": resident_context_voice_knck.persona,
            "author": Author.BOT,
            "flows": default_flows,
            "language": "en",
            "metadata": [],
            "openai_trace_url": None,
            "langsmith_trace_url": None,
        }

        assert mock_log_data_curation_event.call_args_list[9][1] == {
            "chat_session_id": resident_context_voice_knck.ask_request.chat_session_id,
            "conversation_type": resident_context_voice_knck.ask_request.conversation_type,
            "body": "A problem about a package.",
            "call_sid": resident_context_voice_knck.ask_request.product_info.call_sid,
            "property_id": resident_context_voice_knck.property_id,
            "applicant_id": resident_context_voice_knck.ask_request.resident_id,
            "bot_type": resident_context_voice_knck.persona,
            "author": Author.CONTACT,
            "flows": default_flows,
            "language": "en",
            "metadata": [],
            "openai_trace_url": None,
            "langsmith_trace_url": None,
        }

        assert mock_log_data_curation_event.call_args_list[10][1] == {
            "chat_session_id": resident_context_voice_knck.ask_request.chat_session_id,
            "conversation_type": resident_context_voice_knck.ask_request.conversation_type,
            "body": (
                "Thanks for letting me know. I'll connect you to a staff member to assist "
                "you with the package problem. Please standby while I connect you."
            ),
            "call_sid": resident_context_voice_knck.ask_request.product_info.call_sid,
            "property_id": resident_context_voice_knck.property_id,
            "applicant_id": resident_context_voice_knck.ask_request.resident_id,
            "bot_type": resident_context_voice_knck.persona,
            "author": Author.BOT,
            "flows": transfer_to_staff_voice_flows,
            "language": "en",
            "metadata": [],
            "openai_trace_url": None,
            "langsmith_trace_url": None,
        }

        assert mock_log_data_curation_event.call_args_list[11][1] == {
            "chat_session_id": resident_context_voice_knck.ask_request.chat_session_id,
            "conversation_type": resident_context_voice_knck.ask_request.conversation_type,
            "body": "END",
            "call_sid": resident_context_voice_knck.ask_request.product_info.call_sid,
            "property_id": resident_context_voice_knck.property_id,
            "applicant_id": resident_context_voice_knck.ask_request.resident_id,
            "bot_type": resident_context_voice_knck.persona,
            "author": Author.BOT,
            "flows": end_flows,
            "language": "en",
            "openai_trace_url": None,
            "langsmith_trace_url": None,
        }

        # Check the classify_language calls
        expected_classify_calls = [
            mock.call("Hey there!"),
            mock.call("Hey there! How can I assist you today?"),
            mock.call("Create me a service request for my leaking tap in the kitchen."),
            mock.call("Let me look that up for you."),
            mock.call("I've created service request for you. Do you want me to send a text?"),
            mock.call("Thanks, I'm good."),
            mock.call("Alright, no problem at all. How else can I assist you today?"),
            mock.call("Can you connect me to a human agent?"),
            mock.call(
                "I'll be happy to connect you to a staff member to assist you. "
                "Can you provide me a summary of the issue so I can connect you to the right person?"
            ),
            mock.call("A problem about a package."),
            mock.call(
                "Thanks for letting me know. I'll connect you to a staff member to assist "
                "you with the package problem. Please standby while I connect you."
            ),
            # skipped the "END" call
        ]
        mock_classify_language.assert_has_calls(expected_classify_calls)


class TestTranscriptCacheFallback:
    """Tests for the transcript_cache workaround in realtime_history_to_input_item (KNCK-38461)."""

    def test_uses_content_transcript_when_available(self):
        """content[0].transcript takes priority over cache."""
        item = AssistantMessageItem(
            item_id="item_1",
            previous_item_id=None,
            type="message",
            role="assistant",
            status="completed",
            content=[AssistantAudio(type="audio", audio=None, transcript="from content")],
        )
        cache = {"item_1": "from cache"}
        result = realtime_history_to_input_item(item, transcript_cache=cache)
        assert result["content"] == "from content"

    def test_falls_back_to_cache_when_transcript_empty(self):
        """Falls back to cache when content[0].transcript is empty string."""
        item = AssistantMessageItem(
            item_id="item_1",
            previous_item_id=None,
            type="message",
            role="assistant",
            status="completed",
            content=[AssistantAudio(type="audio", audio=None, transcript="")],
        )
        cache = {"item_1": "recovered text"}
        result = realtime_history_to_input_item(item, transcript_cache=cache)
        assert result["content"] == "recovered text"

    def test_falls_back_to_cache_when_transcript_none(self):
        """Falls back to cache when content[0].transcript is None."""
        item = AssistantMessageItem(
            item_id="item_1",
            previous_item_id=None,
            type="message",
            role="assistant",
            status="completed",
            content=[AssistantAudio(type="audio", audio=None, transcript=None)],
        )
        cache = {"item_1": "recovered text"}
        result = realtime_history_to_input_item(item, transcript_cache=cache)
        assert result["content"] == "recovered text"

    def test_returns_none_when_both_empty(self):
        """Returns None when content transcript is empty and cache has no entry."""
        item = AssistantMessageItem(
            item_id="item_1",
            previous_item_id=None,
            type="message",
            role="assistant",
            status="completed",
            content=[AssistantAudio(type="audio", audio=None, transcript="")],
        )
        result = realtime_history_to_input_item(item, transcript_cache={})
        assert result is None

    def test_returns_none_without_cache(self):
        """Returns None when transcript is empty and no cache provided."""
        item = AssistantMessageItem(
            item_id="item_1",
            previous_item_id=None,
            type="message",
            role="assistant",
            status="completed",
            content=[AssistantAudio(type="audio", audio=None, transcript="")],
        )
        result = realtime_history_to_input_item(item)
        assert result is None

    def test_cache_not_used_for_non_message_items(self):
        """Cache is irrelevant for non-message items (tool calls, etc.)."""
        item = RealtimeToolCallItem(
            item_id="item_1",
            call_id="call_1",
            status="completed",
            arguments="{}",
            name="some_tool",
        )
        cache = {"item_1": "should not appear"}
        result = realtime_history_to_input_item(item, transcript_cache=cache)
        assert result is None

    def test_includes_item_id_when_requested(self):
        """Cache fallback still respects include_item_id flag."""
        item = AssistantMessageItem(
            item_id="item_1",
            previous_item_id=None,
            type="message",
            role="assistant",
            status="completed",
            content=[AssistantAudio(type="audio", audio=None, transcript="")],
        )
        cache = {"item_1": "recovered"}
        result = realtime_history_to_input_item(item, include_item_id=True, transcript_cache=cache)
        assert result["item_id"] == "item_1"
        assert result["content"] == "recovered"


class TestRealtimeHistoryToInputListWithTranscriptCache:
    """Tests for transcript_cache passthrough in realtime_history_to_input_list."""

    def test_recovers_truncated_assistant_transcript_via_cache(self):
        """List function passes transcript_cache through to recover SDK-cleared transcripts."""
        history = [
            UserMessageItem(
                item_id="user_1",
                previous_item_id="",
                type="message",
                role="user",
                content=[InputAudio(type="input_audio", audio=None, transcript="Hello")],
                status="completed",
            ),
            AssistantMessageItem(
                item_id="asst_1",
                previous_item_id="user_1",
                type="message",
                role="assistant",
                status="completed",
                content=[AssistantAudio(type="audio", audio=None, transcript="")],
            ),
        ]
        cache = {"asst_1": "Hi there, how can I help?"}
        result = realtime_history_to_input_list(history, transcript_cache=cache)
        assert len(result) == 2
        assert result[1] == {"role": "assistant", "content": "Hi there, how can I help?"}

    def test_without_cache_excludes_empty_transcript(self):
        """Without cache, items with empty transcripts are excluded."""
        history = [
            AssistantMessageItem(
                item_id="asst_1",
                previous_item_id=None,
                type="message",
                role="assistant",
                status="completed",
                content=[AssistantAudio(type="audio", audio=None, transcript="")],
            ),
        ]
        result = realtime_history_to_input_list(history)
        assert result == []

    def test_cache_with_include_item_id(self):
        """Cache works correctly when include_item_id is True."""
        history = [
            AssistantMessageItem(
                item_id="asst_1",
                previous_item_id=None,
                type="message",
                role="assistant",
                status="completed",
                content=[AssistantAudio(type="audio", audio=None, transcript="")],
            ),
        ]
        cache = {"asst_1": "recovered"}
        result = realtime_history_to_input_list(history, include_item_id=True, transcript_cache=cache)
        assert len(result) == 1
        assert result[0] == {"role": "assistant", "content": "recovered", "item_id": "asst_1"}


class TestDataCurationResilience:
    """KNCK-38864: Errors during data curation must not prevent Kafka events from being published.
    The END marker must always fire so calls appear in session viewer."""

    @staticmethod
    def _build_history():
        return [
            UserMessageItem(
                item_id="u1",
                content=[InputAudio(transcript="I need help with my sink")],
            ),
            AssistantMessageItem(
                item_id="a1",
                content=[AssistantAudio(transcript="I can help with that.")],
            ),
        ]

    @mock.patch("agent_leasing.util.realtime_util.log_data_curation_event")
    @mock.patch("agent_leasing.util.realtime_util.classify_language")
    async def test_baseline_kafka_events_published(
        self,
        mock_classify_language,
        mock_log_data_curation_event,
        resident_context_voice_knck,
    ):
        """Baseline: normal classification produces Kafka events for every message + END."""
        from agent_leasing.util.realtime_util import log_data_curation_event_for_realtime_history

        mock_result = mock.Mock()
        mock_result.language_code = "en"
        mock_classify_language.return_value = mock_result

        await log_data_curation_event_for_realtime_history(self._build_history(), resident_context_voice_knck)

        # 2 message events + 1 END = 3 Kafka publishes
        assert mock_log_data_curation_event.call_count == 3
        bodies = [c.kwargs["body"] for c in mock_log_data_curation_event.call_args_list]
        assert bodies == ["I need help with my sink", "I can help with that.", "END"]

    @mock.patch("agent_leasing.util.realtime_util.log_data_curation_event")
    @mock.patch(
        "agent_leasing.util.realtime_util.classify_language",
        side_effect=asyncio.CancelledError(),
    )
    async def test_cancelled_error_in_classify_language_still_publishes_kafka_events(
        self,
        _mock_classify_language,
        mock_log_data_curation_event,
        resident_context_voice_knck,
    ):
        """When classify_language raises CancelledError (e.g. anyio bug #695 during teardown),
        the same Kafka events must still be published with fallback language codes."""
        from agent_leasing.util.realtime_util import log_data_curation_event_for_realtime_history

        await log_data_curation_event_for_realtime_history(self._build_history(), resident_context_voice_knck)

        # Same 3 events as baseline
        assert mock_log_data_curation_event.call_count == 3
        bodies = [c.kwargs["body"] for c in mock_log_data_curation_event.call_args_list]
        assert bodies == ["I need help with my sink", "I can help with that.", "END"]

        # Falls back to "en" for all events
        for call in mock_log_data_curation_event.call_args_list:
            assert call.kwargs["language"] == "en"

    @mock.patch("agent_leasing.util.realtime_util.log_data_curation_event")
    @mock.patch("agent_leasing.util.realtime_util.classify_language")
    async def test_error_in_message_logging_still_publishes_end_marker(
        self,
        mock_classify_language,
        mock_log_data_curation_event,
        resident_context_voice_knck,
    ):
        """If _log_message_events raises, the END marker still fires via try/finally."""
        from agent_leasing.util.realtime_util import log_data_curation_event_for_realtime_history

        mock_result = mock.Mock()
        mock_result.language_code = "en"
        mock_classify_language.return_value = mock_result

        # Both message events succeed, then simulate an error propagating from _log_message_events
        # by having the first call fail — the outer try/except catches it, finally publishes END
        mock_log_data_curation_event.side_effect = [
            RuntimeError("unexpected"),  # item 1
            None,  # END (via finally)
        ]

        await log_data_curation_event_for_realtime_history(self._build_history(), resident_context_voice_knck)

        # END marker was published despite the error
        assert mock_log_data_curation_event.call_args_list[-1].kwargs["body"] == "END"


class TestFrustrationClassifierIntegration:
    """Voice frustration classifier runs in parallel with language
    classification at end-of-call and publishes a single FRUSTRATED_USER
    TaskActivityEvent when the classifier flags the conversation."""

    @staticmethod
    def _build_history():
        return [
            UserMessageItem(
                item_id="u1",
                content=[InputAudio(transcript="My AC has been broken for two weeks")],
            ),
            AssistantMessageItem(
                item_id="a1",
                content=[AssistantAudio(transcript="Let me file a service request.")],
            ),
            UserMessageItem(
                item_id="u2",
                content=[InputAudio(transcript="I called twice. Get me a manager.")],
            ),
        ]

    @mock.patch("agent_leasing.util.realtime_util.log_data_curation_event")
    @mock.patch("agent_leasing.util.realtime_util.publish_task_activity")
    @mock.patch("agent_leasing.util.realtime_util.classify_frustration")
    @mock.patch("agent_leasing.util.realtime_util.classify_language")
    async def test_frustrated_conversation_publishes_activity(
        self,
        mock_classify_language,
        mock_classify_frustration,
        mock_publish,
        _mock_log,
        resident_context_voice_knck,
    ):
        from agent_leasing.kafka.task_activity.extractors import extract_frustrated_user_events
        from agent_leasing.util.realtime_util import log_data_curation_event_for_realtime_history

        mock_classify_language.return_value = mock.Mock(language_code="en")
        mock_classify_frustration.return_value = mock.Mock(
            is_frustrated=True,
            trigger_message="I called twice. Get me a manager.",
        )

        await log_data_curation_event_for_realtime_history(self._build_history(), resident_context_voice_knck)

        # Classifier was given the assembled transcript with role tags.
        (transcript_arg,), _ = mock_classify_frustration.call_args
        assert "Resident: My AC has been broken for two weeks" in transcript_arg
        assert "Assistant: Let me file a service request." in transcript_arg
        assert "Resident: I called twice. Get me a manager." in transcript_arg

        # publish_task_activity called once with the frustration extractor.
        mock_publish.assert_called_once()
        args, kwargs = mock_publish.call_args
        assert args[0] is extract_frustrated_user_events
        assert args[1] is True  # user_frustrated arg
        assert args[2] is resident_context_voice_knck
        # Classifier-supplied trigger message wins over first-user fallback.
        assert kwargs["user_message"] == "I called twice. Get me a manager."

    @mock.patch("agent_leasing.util.realtime_util.log_data_curation_event")
    @mock.patch("agent_leasing.util.realtime_util.publish_task_activity")
    @mock.patch("agent_leasing.util.realtime_util.classify_frustration")
    @mock.patch("agent_leasing.util.realtime_util.classify_language")
    async def test_falls_back_to_last_user_message_when_trigger_empty(
        self,
        mock_classify_language,
        mock_classify_frustration,
        mock_publish,
        _mock_log,
        resident_context_voice_knck,
    ):
        # When the classifier doesn't supply a trigger_message, fall back
        # to the LAST user message — the most recent turn is the most
        # likely trigger; the first turn is usually a polite opener.
        from agent_leasing.util.realtime_util import log_data_curation_event_for_realtime_history

        mock_classify_language.return_value = mock.Mock(language_code="en")
        mock_classify_frustration.return_value = mock.Mock(is_frustrated=True, trigger_message="")

        await log_data_curation_event_for_realtime_history(self._build_history(), resident_context_voice_knck)

        mock_publish.assert_called_once()
        kwargs = mock_publish.call_args.kwargs
        assert kwargs["user_message"] == "I called twice. Get me a manager."

    @mock.patch("agent_leasing.util.realtime_util.log_data_curation_event")
    @mock.patch("agent_leasing.util.realtime_util.publish_task_activity")
    @mock.patch("agent_leasing.util.realtime_util.classify_frustration")
    @mock.patch("agent_leasing.util.realtime_util.classify_language")
    async def test_calm_conversation_does_not_publish(
        self,
        mock_classify_language,
        mock_classify_frustration,
        mock_publish,
        _mock_log,
        resident_context_voice_knck,
    ):
        from agent_leasing.util.realtime_util import log_data_curation_event_for_realtime_history

        mock_classify_language.return_value = mock.Mock(language_code="en")
        mock_classify_frustration.return_value = mock.Mock(is_frustrated=False)

        await log_data_curation_event_for_realtime_history(self._build_history(), resident_context_voice_knck)

        mock_publish.assert_not_called()

    @mock.patch("agent_leasing.util.realtime_util.log_data_curation_event")
    @mock.patch("agent_leasing.util.realtime_util.publish_task_activity")
    @mock.patch(
        "agent_leasing.util.realtime_util.classify_frustration",
        side_effect=asyncio.CancelledError(),
    )
    @mock.patch("agent_leasing.util.realtime_util.classify_language")
    async def test_classifier_cancellation_falls_back_silently(
        self,
        mock_classify_language,
        _mock_classify_frustration,
        mock_publish,
        _mock_log,
        resident_context_voice_knck,
    ):
        """anyio teardown can inject CancelledError; the data-curation flow
        must keep running and just skip the frustration emit."""
        from agent_leasing.util.realtime_util import log_data_curation_event_for_realtime_history

        mock_classify_language.return_value = mock.Mock(language_code="en")

        await log_data_curation_event_for_realtime_history(self._build_history(), resident_context_voice_knck)

        mock_publish.assert_not_called()
