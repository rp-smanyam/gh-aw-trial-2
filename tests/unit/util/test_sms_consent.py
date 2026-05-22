from unittest.mock import AsyncMock, Mock, patch

import pytest

from agent_leasing.models.context import SessionScope
from agent_leasing.util.sms_consent import GateResult, classify_opt_out_intent, handle_sms_consent_gate


class TestSmsConsentGate:
    @pytest.fixture
    def sms_context(self, ask_request_resident_sms_knck):
        return SessionScope(ask_request=ask_request_resident_sms_knck)

    @pytest.fixture
    def mock_mcp_server(self):
        return AsyncMock()

    # --- GRANTED status tests ---

    @pytest.mark.asyncio
    @patch("agent_leasing.util.sms_consent.fetch_sms_consent_status")
    async def test_granted_stop_keyword_returns_opt_out_message(self, mock_fetch, sms_context, mock_mcp_server):
        """STOP from granted status revokes consent and returns opt-out message (blocks agent)."""
        sms_context.ask_request.prompt = "STOP"
        mock_fetch.return_value = "granted"

        result = await handle_sms_consent_gate(sms_context.ask_request, sms_context, mock_mcp_server)

        assert isinstance(result, GateResult)
        assert result.action == "return_message"
        assert "opted out" in result.message.lower()
        assert "START" in result.message
        assert sms_context.sms_consent_status == "revoked"
        mock_mcp_server.call_tool.assert_called_once()

    @pytest.mark.asyncio
    @patch("agent_leasing.util.sms_consent.fetch_sms_consent_status")
    @patch("agent_leasing.util.sms_consent.classify_opt_out_intent")
    async def test_granted_stop_with_extra_text_uses_classifier(
        self, mock_classify, mock_fetch, sms_context, mock_mcp_server
    ):
        """STOP in longer input is not treated as explicit STOP keyword."""
        from agent_leasing.util.sms_consent import OptOutClassification

        sms_context.ask_request.prompt = "Please STOP sending updates."
        mock_fetch.return_value = "granted"
        mock_classify.return_value = OptOutClassification(is_opt_out=False, reasoning="Not opt-out")

        result = await handle_sms_consent_gate(sms_context.ask_request, sms_context, mock_mcp_server)

        assert result.action == "proceed"
        assert result.message is None
        assert sms_context.sms_consent_status == "granted"
        mock_classify.assert_called_once_with("Please STOP sending updates.")

    @pytest.mark.asyncio
    @patch("agent_leasing.util.sms_consent.fetch_sms_consent_status")
    @patch("agent_leasing.util.sms_consent.classify_opt_out_intent")
    async def test_granted_stop_with_texting_revokes_without_classifier(
        self, mock_classify, mock_fetch, sms_context, mock_mcp_server
    ):
        """Only exact STOP+texting command should be treated as keyword opt-out."""
        sms_context.ask_request.prompt = "STOP texting"
        mock_fetch.return_value = "granted"

        result = await handle_sms_consent_gate(sms_context.ask_request, sms_context, mock_mcp_server)

        assert result.action == "return_message"
        assert "opted out" in result.message.lower()
        assert sms_context.sms_consent_status == "revoked"
        mock_classify.assert_not_called()

    @pytest.mark.asyncio
    @patch("agent_leasing.util.sms_consent.fetch_sms_consent_status")
    @patch("agent_leasing.util.sms_consent.classify_opt_out_intent")
    async def test_granted_stop_text_keyword_revokes_without_classifier(
        self, mock_classify, mock_fetch, sms_context, mock_mcp_server
    ):
        """Exact 'STOP text' command triggers opt-out without classifier."""
        sms_context.ask_request.prompt = "STOP text"
        mock_fetch.return_value = "granted"

        result = await handle_sms_consent_gate(sms_context.ask_request, sms_context, mock_mcp_server)

        assert result.action == "return_message"
        assert "opted out" in result.message.lower()
        assert sms_context.sms_consent_status == "revoked"
        mock_classify.assert_not_called()

    @pytest.mark.asyncio
    @patch("agent_leasing.util.sms_consent.fetch_sms_consent_status")
    @patch("agent_leasing.util.sms_consent.classify_opt_out_intent")
    async def test_granted_stop_with_period_revokes_without_classifier(
        self, mock_classify, mock_fetch, sms_context, mock_mcp_server
    ):
        """STOP with trailing period strips punctuation and matches exact keyword — no classifier."""
        sms_context.ask_request.prompt = "stop."
        mock_fetch.return_value = "granted"

        result = await handle_sms_consent_gate(sms_context.ask_request, sms_context, mock_mcp_server)

        assert result.action == "return_message"
        assert "opted out" in result.message.lower()
        assert sms_context.sms_consent_status == "revoked"
        mock_classify.assert_not_called()

    @pytest.mark.asyncio
    @patch("agent_leasing.util.sms_consent.fetch_sms_consent_status")
    @patch("agent_leasing.util.sms_consent.classify_opt_out_intent")
    async def test_granted_stop_with_punctuation_revokes_without_classifier(
        self, mock_classify, mock_fetch, sms_context, mock_mcp_server
    ):
        """STOP with trailing punctuation (e.g. STOP!) strips punctuation and matches exact keyword — no classifier."""
        sms_context.ask_request.prompt = "STOP!"
        mock_fetch.return_value = "granted"

        result = await handle_sms_consent_gate(sms_context.ask_request, sms_context, mock_mcp_server)

        assert result.action == "return_message"
        assert "opted out" in result.message.lower()
        assert sms_context.sms_consent_status == "revoked"
        mock_classify.assert_not_called()

    @pytest.mark.asyncio
    @patch("agent_leasing.util.sms_consent.fetch_sms_consent_status")
    @patch("agent_leasing.util.sms_consent.classify_opt_out_intent")
    async def test_granted_stop_texting_with_punctuation_revokes_without_classifier(
        self, mock_classify, mock_fetch, sms_context, mock_mcp_server
    ):
        """STOP texting with trailing punctuation strips punctuation and matches exact keyword."""
        sms_context.ask_request.prompt = "STOP texting!"
        mock_fetch.return_value = "granted"

        result = await handle_sms_consent_gate(sms_context.ask_request, sms_context, mock_mcp_server)

        assert result.action == "return_message"
        assert "opted out" in result.message.lower()
        assert sms_context.sms_consent_status == "revoked"
        mock_classify.assert_not_called()

    @pytest.mark.asyncio
    @patch("agent_leasing.util.sms_consent.fetch_sms_consent_status")
    @patch("agent_leasing.util.sms_consent.classify_opt_out_intent")
    async def test_granted_stop_in_issue_sentence_does_not_keyword_opt_out(
        self, mock_classify, mock_fetch, sms_context, mock_mcp_server
    ):
        """STOP inside issue text should not trigger keyword opt-out."""
        from agent_leasing.util.sms_consent import OptOutClassification

        sms_context.ask_request.prompt = "My geyser stop working"
        mock_fetch.return_value = "granted"
        mock_classify.return_value = OptOutClassification(is_opt_out=False, reasoning="Not opt-out")

        result = await handle_sms_consent_gate(sms_context.ask_request, sms_context, mock_mcp_server)

        assert result.action == "proceed"
        assert result.message is None
        assert sms_context.sms_consent_status == "granted"
        mock_classify.assert_called_once_with("My geyser stop working")

    @pytest.mark.asyncio
    @patch("agent_leasing.util.sms_consent.fetch_sms_consent_status")
    @patch("agent_leasing.util.sms_consent.classify_opt_out_intent")
    async def test_granted_opt_out_intent_returns_message(
        self, mock_classify, mock_fetch, sms_context, mock_mcp_server
    ):
        """Natural language opt-out from granted status returns opt-out message."""
        from agent_leasing.util.sms_consent import OptOutClassification

        sms_context.ask_request.prompt = "Please stop texting me"
        mock_fetch.return_value = "granted"
        mock_classify.return_value = OptOutClassification(is_opt_out=True, reasoning="Opt-out")

        result = await handle_sms_consent_gate(sms_context.ask_request, sms_context, mock_mcp_server)

        assert result.action == "return_message"
        assert "opted out" in result.message.lower()
        assert sms_context.sms_consent_status == "revoked"

    @pytest.mark.asyncio
    @patch("agent_leasing.util.sms_consent.fetch_sms_consent_status")
    @patch("agent_leasing.util.sms_consent.classify_opt_out_intent")
    async def test_granted_normal_question_proceeds(self, mock_classify, mock_fetch, sms_context, mock_mcp_server):
        """Normal question from granted status proceeds to agent."""
        from agent_leasing.util.sms_consent import OptOutClassification

        sms_context.ask_request.prompt = "What's my balance?"
        mock_fetch.return_value = "granted"
        mock_classify.return_value = OptOutClassification(is_opt_out=False, reasoning="Not opt-out")

        result = await handle_sms_consent_gate(sms_context.ask_request, sms_context, mock_mcp_server)

        assert result.action == "proceed"
        assert result.message is None

    # --- NOT GRANTED (new) status tests ---

    @pytest.mark.asyncio
    async def test_new_status_without_start_returns_consent_message(self, sms_context, mock_mcp_server):
        """New status without START returns consent request message (blocks agent)."""
        sms_context.sms_consent_recorded = False
        sms_context.ask_request.prompt = "What's my balance?"
        mock_mcp_server.call_tool.return_value = Mock(
            structuredContent={"sms_consent": {"status": "new"}}, isError=False
        )

        with patch("agent_leasing.util.sms_consent.classify_opt_out_intent") as mock_classify:
            from agent_leasing.util.sms_consent import OptOutClassification

            mock_classify.return_value = OptOutClassification(is_opt_out=False, reasoning="Not opt-out")
            result = await handle_sms_consent_gate(sms_context.ask_request, sms_context, mock_mcp_server)

        assert result.action == "return_message"
        assert "not opted in" in result.message.lower()
        assert "START" in result.message
        assert "STOP" in result.message
        assert sms_context.pending_sms_query == "What's my balance?"
        assert sms_context.sms_needs_consent_prompt is True

    @pytest.mark.asyncio
    async def test_new_status_with_start_proceeds(self, sms_context, mock_mcp_server):
        """START from new status grants consent and proceeds to agent."""
        sms_context.sms_consent_recorded = False
        sms_context.ask_request.prompt = "START"
        mock_mcp_server.call_tool.return_value = Mock(
            structuredContent={"sms_consent": {"status": "new"}}, isError=False
        )

        result = await handle_sms_consent_gate(sms_context.ask_request, sms_context, mock_mcp_server)

        assert result.action == "proceed"
        assert sms_context.sms_consent_status == "granted"

    @pytest.mark.asyncio
    async def test_new_status_with_start_and_punctuation_proceeds(self, sms_context, mock_mcp_server):
        """START with punctuation/extra text still grants consent and proceeds."""
        sms_context.sms_consent_recorded = False
        sms_context.ask_request.prompt = "start. please opt me in"
        mock_mcp_server.call_tool.return_value = Mock(
            structuredContent={"sms_consent": {"status": "new"}}, isError=False
        )

        result = await handle_sms_consent_gate(sms_context.ask_request, sms_context, mock_mcp_server)

        assert result.action == "proceed"
        assert sms_context.sms_consent_status == "granted"

    @pytest.mark.asyncio
    @patch("agent_leasing.util.sms_consent.fetch_sms_consent_status")
    @patch("agent_leasing.util.sms_consent.classify_opt_out_intent")
    async def test_start_mid_sentence_does_not_grant_consent(
        self, mock_classify, mock_fetch, sms_context, mock_mcp_server
    ):
        """START appearing mid-sentence (e.g. 'my car won't start') does NOT grant consent."""
        from agent_leasing.util.sms_consent import OptOutClassification

        sms_context.ask_request.prompt = "my car won't start"
        mock_fetch.return_value = "new"
        mock_classify.return_value = OptOutClassification(is_opt_out=False, reasoning="Not opt-out")

        result = await handle_sms_consent_gate(sms_context.ask_request, sms_context, mock_mcp_server)

        assert result.action == "return_message"
        assert "not opted in" in result.message.lower()
        assert sms_context.sms_consent_status == "new"

    @pytest.mark.asyncio
    async def test_new_status_with_stop_returns_opt_out_message(self, sms_context, mock_mcp_server):
        """STOP from new status revokes and returns opt-out message."""
        sms_context.sms_consent_recorded = False
        sms_context.ask_request.prompt = "STOP"
        mock_mcp_server.call_tool.return_value = Mock(
            structuredContent={"sms_consent": {"status": "new"}}, isError=False
        )

        result = await handle_sms_consent_gate(sms_context.ask_request, sms_context, mock_mcp_server)

        assert result.action == "return_message"
        assert "opted out" in result.message.lower()
        assert sms_context.sms_consent_status == "revoked"

    @pytest.mark.asyncio
    async def test_new_status_with_stop_and_texting_returns_opt_out_message(self, sms_context, mock_mcp_server):
        """STOP + texting from new status revokes and returns opt-out message."""
        sms_context.sms_consent_recorded = False
        sms_context.ask_request.prompt = "STOP texting"
        mock_mcp_server.call_tool.return_value = Mock(
            structuredContent={"sms_consent": {"status": "new"}}, isError=False
        )

        result = await handle_sms_consent_gate(sms_context.ask_request, sms_context, mock_mcp_server)

        assert result.action == "return_message"
        assert "opted out" in result.message.lower()
        assert sms_context.sms_consent_status == "revoked"

    @pytest.mark.asyncio
    async def test_new_status_subsequent_message_returns_consent_message(self, sms_context, mock_mcp_server):
        """Subsequent messages with new status still block agent with consent message."""
        sms_context.sms_consent_recorded = True  # subsequent message
        sms_context.sms_consent_status = "new"
        sms_context.ask_request.prompt = "What's my balance?"
        mock_mcp_server.call_tool.return_value = Mock(
            structuredContent={"sms_consent": {"status": "new"}}, isError=False
        )

        result = await handle_sms_consent_gate(sms_context.ask_request, sms_context, mock_mcp_server)

        assert result.action == "return_message"
        assert "not opted in" in result.message.lower()
        assert sms_context.pending_sms_query == "What's my balance?"
        assert sms_context.sms_needs_consent_prompt is True

    @pytest.mark.asyncio
    async def test_pending_query_overwritten_by_new_non_start_message(self, sms_context, mock_mcp_server):
        """Non-START message overwrites existing pending query when consent not granted."""
        sms_context.sms_consent_recorded = True
        sms_context.sms_consent_status = "new"
        sms_context.pending_sms_query = "Old question"
        sms_context.sms_needs_consent_prompt = True
        sms_context.ask_request.prompt = "New question"
        mock_mcp_server.call_tool.return_value = Mock(
            structuredContent={"sms_consent": {"status": "new"}}, isError=False
        )

        result = await handle_sms_consent_gate(sms_context.ask_request, sms_context, mock_mcp_server)

        assert result.action == "return_message"
        assert sms_context.pending_sms_query == "New question"
        assert sms_context.sms_needs_consent_prompt is True

    # --- REVOKED status tests ---

    @pytest.mark.asyncio
    @patch("agent_leasing.util.sms_consent.fetch_sms_consent_status")
    async def test_revoked_status_returns_opt_out_message(self, mock_fetch, sms_context, mock_mcp_server):
        """Revoked status blocks agent and returns opt-out message."""
        sms_context.ask_request.prompt = "What's my balance?"
        mock_fetch.return_value = "revoked"

        result = await handle_sms_consent_gate(sms_context.ask_request, sms_context, mock_mcp_server)

        assert result.action == "return_message"
        assert "opted out" in result.message.lower()
        assert "START" in result.message
        assert sms_context.sms_consent_status == "revoked"

    @pytest.mark.asyncio
    @patch("agent_leasing.util.sms_consent.fetch_sms_consent_status")
    async def test_revoked_status_start_keyword_proceeds(self, mock_fetch, sms_context, mock_mcp_server):
        """START from revoked status grants consent and proceeds to agent."""
        sms_context.ask_request.prompt = "START"
        mock_fetch.return_value = "revoked"

        result = await handle_sms_consent_gate(sms_context.ask_request, sms_context, mock_mcp_server)

        assert result.action == "proceed"
        assert sms_context.sms_consent_status == "granted"
        mock_mcp_server.call_tool.assert_called_once()

    # --- DECLINED status tests (treated same as revoked) ---

    @pytest.mark.asyncio
    @patch("agent_leasing.util.sms_consent.fetch_sms_consent_status")
    async def test_declined_status_returns_opt_out_message(self, mock_fetch, sms_context, mock_mcp_server):
        """Declined status behaves identically to revoked - blocks agent."""
        sms_context.ask_request.prompt = "What's my balance?"
        mock_fetch.return_value = "declined"

        result = await handle_sms_consent_gate(sms_context.ask_request, sms_context, mock_mcp_server)

        assert result.action == "return_message"
        assert "opted out" in result.message.lower()
        assert "START" in result.message
        assert sms_context.sms_consent_status == "declined"

    @pytest.mark.asyncio
    @patch("agent_leasing.util.sms_consent.fetch_sms_consent_status")
    async def test_declined_status_start_keyword_proceeds(self, mock_fetch, sms_context, mock_mcp_server):
        """START from declined status grants consent and proceeds to agent."""
        sms_context.ask_request.prompt = "START"
        mock_fetch.return_value = "declined"

        result = await handle_sms_consent_gate(sms_context.ask_request, sms_context, mock_mcp_server)

        assert result.action == "proceed"
        assert sms_context.sms_consent_status == "granted"
        mock_mcp_server.call_tool.assert_called_once()

    # --- START from already-granted status ---

    @pytest.mark.asyncio
    @patch("agent_leasing.util.sms_consent.fetch_sms_consent_status")
    async def test_start_from_granted_does_not_re_grant(self, mock_fetch, sms_context, mock_mcp_server):
        """START from already-granted status proceeds without calling update MCP."""
        sms_context.ask_request.prompt = "START"
        mock_fetch.return_value = "granted"

        result = await handle_sms_consent_gate(sms_context.ask_request, sms_context, mock_mcp_server)

        assert result.action == "proceed"
        assert sms_context.sms_consent_status == "granted"
        mock_mcp_server.call_tool.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_with_pending_query_replays_and_clears(self, sms_context, mock_mcp_server):
        """START with pending query clears flags and replays the stored query."""
        sms_context.sms_consent_recorded = True
        sms_context.sms_consent_status = "new"
        sms_context.pending_sms_query = "What is my rent?"
        sms_context.sms_needs_consent_prompt = True
        sms_context.ask_request.prompt = "START"
        mock_mcp_server.call_tool.return_value = Mock(isError=False, content=[])

        result = await handle_sms_consent_gate(sms_context.ask_request, sms_context, mock_mcp_server)

        assert result.action == "proceed"
        assert sms_context.sms_consent_status == "granted"
        assert sms_context.pending_sms_query is None
        assert sms_context.sms_needs_consent_prompt is False
        assert sms_context.ask_request.prompt.startswith(
            "Please greet the user with a welcome message, then answer their question: "
        )

    # --- LLM failure handling ---

    @pytest.mark.asyncio
    @patch("agent_leasing.util.sms_consent.fetch_sms_consent_status")
    @patch("agent_leasing.util.sms_consent.classify_opt_out_intent")
    async def test_llm_failure_from_granted_proceeds(self, mock_classify, mock_fetch, sms_context, mock_mcp_server):
        """LLM failure from granted status proceeds normally (user can use STOP keyword)."""
        sms_context.ask_request.prompt = "no thanks"
        mock_fetch.return_value = "granted"
        mock_classify.side_effect = Exception("LLM error")

        result = await handle_sms_consent_gate(sms_context.ask_request, sms_context, mock_mcp_server)

        assert result.action == "proceed"
        assert sms_context.sms_consent_status == "granted"

    # --- MCP failure handling ---

    @pytest.mark.asyncio
    @patch("agent_leasing.util.sms_consent.classify_opt_out_intent")
    async def test_mcp_fetch_failure_returns_opt_out_message(self, mock_classify, sms_context, mock_mcp_server):
        """MCP fetch failure returns opt-out message (fail safe)."""
        sms_context.sms_consent_recorded = False
        sms_context.ask_request.prompt = "Hello"
        mock_mcp_server.call_tool.side_effect = Exception("MCP unavailable")

        result = await handle_sms_consent_gate(sms_context.ask_request, sms_context, mock_mcp_server)

        # Gate treats None as not-granted and returns consent request message
        assert result.action == "return_message"

    # --- Fresh status fetch on every turn ---

    @pytest.mark.asyncio
    @patch("agent_leasing.util.sms_consent.fetch_sms_consent_status")
    @patch("agent_leasing.util.sms_consent.classify_opt_out_intent")
    async def test_always_fetches_fresh_status(self, mock_classify, mock_fetch, sms_context, mock_mcp_server):
        """Every turn fetches fresh status from backend (no caching)."""
        from agent_leasing.util.sms_consent import OptOutClassification

        sms_context.sms_consent_recorded = True
        sms_context.ask_request.prompt = "What's my balance?"
        mock_fetch.return_value = "granted"
        mock_classify.return_value = OptOutClassification(is_opt_out=False, reasoning="Not opt-out")

        result = await handle_sms_consent_gate(sms_context.ask_request, sms_context, mock_mcp_server)

        assert result.action == "proceed"
        assert mock_fetch.called

    # --- Update consent API tests ---

    @pytest.mark.asyncio
    @patch("agent_leasing.util.sms_consent.fetch_sms_consent_status")
    async def test_update_consent_includes_source_old_api(self, mock_fetch, sms_context, mock_mcp_server):
        """Source='renter-ai' is passed when updating SMS consent (old API format)."""
        sms_context.ask_request.prompt = "STOP"
        mock_fetch.return_value = "granted"

        await handle_sms_consent_gate(sms_context.ask_request, sms_context, mock_mcp_server)

        mock_mcp_server.call_tool.assert_called_once_with(
            "update_resident_sms_consent_information",
            {
                "request": {
                    "resident_id": int(sms_context.ask_request.product_info.knock_resident_id),
                    "sms_consent": False,
                    "source": "renter-ai",
                }
            },
        )

    # --- Gate exception handling ---

    @pytest.mark.asyncio
    @patch("agent_leasing.util.sms_consent.fetch_sms_consent_status")
    async def test_gate_exception_returns_opt_out_message(self, mock_fetch, sms_context, mock_mcp_server):
        """Gate exception returns opt-out message and sets revoked status."""
        mock_fetch.side_effect = Exception("Unexpected error")

        result = await handle_sms_consent_gate(sms_context.ask_request, sms_context, mock_mcp_server)

        assert result.action == "return_message"
        assert "opted out" in result.message.lower()
        assert sms_context.sms_consent_status == "revoked"


class TestSmsConsentMessageHelpers:
    """Test the message helper functions directly."""

    def test_opt_out_message_english(self):
        from agent_leasing.util.sms_consent import _get_opt_out_message

        msg = _get_opt_out_message("Hello")
        assert "opted out" in msg.lower()
        assert "START" in msg

    def test_opt_out_message_spanish(self):
        from agent_leasing.util.sms_consent import _get_opt_out_message

        msg = _get_opt_out_message("No quiero recibir mensajes de texto")
        assert "inscrito" in msg.lower()
        assert "START" in msg

    def test_consent_request_new_status(self):
        from agent_leasing.util.sms_consent import _get_consent_request_message

        msg = _get_consent_request_message("Hello", "new")
        assert "not opted in" in msg.lower()
        assert "START" in msg
        assert "STOP" in msg

    def test_consent_request_revoked_status(self):
        from agent_leasing.util.sms_consent import _get_consent_request_message

        msg = _get_consent_request_message("Hello", "revoked")
        assert "opted out" in msg.lower()
        assert "START" in msg

    def test_consent_request_declined_status(self):
        from agent_leasing.util.sms_consent import _get_consent_request_message

        msg = _get_consent_request_message("Hello", "declined")
        assert "opted out" in msg.lower()
        assert "START" in msg

    def test_detect_language_short_input(self):
        from agent_leasing.util.sms_consent import _detect_language

        assert _detect_language("Hi") == "en"
        assert _detect_language("START") == "en"
        assert _detect_language("STOP") == "en"

    def test_detect_language_spanish(self):
        from agent_leasing.util.sms_consent import _detect_language

        assert _detect_language("Cuál es mi renta este mes") == "es"
        assert _detect_language("Hola, necesito ayuda con mi cuenta") == "es"

    def test_detect_language_english_with_false_positives(self):
        """Ensure English words containing Spanish substrings are not misdetected."""
        from agent_leasing.util.sms_consent import _detect_language

        assert _detect_language("What is my balance this month") == "en"

    def test_detect_language_english_default(self):
        from agent_leasing.util.sms_consent import _detect_language

        assert _detect_language("What is my rent this month") == "en"


@pytest.mark.asyncio
@pytest.mark.skip(reason="Integration test - requires real LLM API calls, flaky in CI")
class TestOptOutClassification:
    """Integration tests for opt-out classification using real LLM (requires API key)."""

    # False Positives - These should NOT trigger opt-out
    async def test_no_thank_you_not_opt_out(self):
        """'No thank you' without SMS context should NOT trigger opt-out."""
        classification = await classify_opt_out_intent("No thank you")
        assert classification.is_opt_out is False

    async def test_no_thanks_with_new_request_not_opt_out(self):
        """'No thanks' followed by new request should NOT trigger opt-out."""
        classification = await classify_opt_out_intent("No thanks. Do I have any packages?")
        assert classification.is_opt_out is False

    async def test_simple_no_not_opt_out(self):
        """Simple 'no' should NOT trigger opt-out."""
        classification = await classify_opt_out_intent("no")
        assert classification.is_opt_out is False

    async def test_not_interested_not_opt_out(self):
        """'Not interested' without SMS context should NOT trigger opt-out."""
        classification = await classify_opt_out_intent("not interested")
        assert classification.is_opt_out is False

    async def test_maybe_later_not_opt_out(self):
        """'Maybe later' should NOT trigger opt-out."""
        classification = await classify_opt_out_intent("maybe later")
        assert classification.is_opt_out is False

    async def test_im_good_not_opt_out(self):
        """'I'm good' should NOT trigger opt-out."""
        classification = await classify_opt_out_intent("I'm good")
        assert classification.is_opt_out is False

    async def test_nevermind_not_opt_out(self):
        """'Nevermind' should NOT trigger opt-out."""
        classification = await classify_opt_out_intent("nevermind")
        assert classification.is_opt_out is False

    # True Positives - These SHOULD trigger opt-out
    async def test_stop_texting_is_opt_out(self):
        """'Stop texting me' should trigger opt-out."""
        classification = await classify_opt_out_intent("stop texting me")
        assert classification.is_opt_out is True

    async def test_no_more_texts_is_opt_out(self):
        """'No more texts' should trigger opt-out."""
        classification = await classify_opt_out_intent("no more texts")
        assert classification.is_opt_out is True

    async def test_unsubscribe_is_opt_out(self):
        """'Unsubscribe' should trigger opt-out."""
        classification = await classify_opt_out_intent("unsubscribe")
        assert classification.is_opt_out is True

    async def test_dont_message_me_is_opt_out(self):
        """'Don't message me anymore' should trigger opt-out."""
        classification = await classify_opt_out_intent("don't message me anymore")
        assert classification.is_opt_out is True

    async def test_i_dont_want_texts_is_opt_out(self):
        """'I don't want texts' with SMS context should opt-out."""
        classification = await classify_opt_out_intent("I don't want texts")
        assert classification.is_opt_out is True

    async def test_please_stop_messaging_is_opt_out(self):
        """'Please stop messaging me' should trigger opt-out."""
        classification = await classify_opt_out_intent("please stop messaging me")
        assert classification.is_opt_out is True

    # Edge Cases
    async def test_i_dont_want_ambiguous_not_opt_out(self):
        """'I don't want that' without 'texts' context is ambiguous - should NOT opt-out."""
        classification = await classify_opt_out_intent("I don't want that")
        assert classification.is_opt_out is False

    async def test_stop_without_context_not_opt_out(self):
        """'Stop' alone is ambiguous without SMS context - should NOT opt-out.

        Note: The uppercase 'STOP' keyword is handled separately via string match,
        not by the LLM classifier. This test verifies the LLM doesn't over-trigger
        on ambiguous conversational uses of 'stop'.
        """
        classification = await classify_opt_out_intent("stop")
        assert classification.is_opt_out is False
