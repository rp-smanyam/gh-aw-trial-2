import pytest
from agents.tracing import default_exporter

from agent_leasing.clients import openai as openai_client_module
from agent_leasing.settings import Config, settings


def test_initialize_openai_client_sets_tracing_endpoint() -> None:
    original_base_url = settings.openai_base_url
    original_endpoint = default_exporter().endpoint
    original_client = openai_client_module._openai_client

    try:
        settings.openai_base_url = "https://us.api.openai.com/v1"
        openai_client_module._openai_client = None

        client = openai_client_module.initialize_openai_client()
        assert client is openai_client_module._openai_client
        assert default_exporter().endpoint == "https://us.api.openai.com/v1/traces/ingest"
        assert openai_client_module.initialize_openai_client() is client
    finally:
        settings.openai_base_url = original_base_url
        default_exporter().endpoint = original_endpoint
        openai_client_module._openai_client = original_client


def test_initialize_openai_client_without_base_url() -> None:
    original_base_url = settings.openai_base_url
    original_endpoint = default_exporter().endpoint
    original_client = openai_client_module._openai_client

    try:
        settings.openai_base_url = ""
        openai_client_module._openai_client = None

        client = openai_client_module.initialize_openai_client()
        assert client is openai_client_module._openai_client
        # Endpoint should not be modified when base_url is not set
        assert default_exporter().endpoint == original_endpoint
    finally:
        settings.openai_base_url = original_base_url
        default_exporter().endpoint = original_endpoint
        openai_client_module._openai_client = original_client


def test_openai_base_url_validation_http() -> None:
    """Test that openai_base_url must start with http:// or https://"""
    # Pydantic's HttpUrl will reject non-HTTP(S) URLs
    with pytest.raises(ValueError, match="Invalid openai_base_url.*Must be a valid HTTP or HTTPS URL"):
        Config(openai_base_url="ftp://invalid.com")


def test_openai_base_url_validation_malformed() -> None:
    """Test that openai_base_url must be well-formed"""
    # Pydantic's HttpUrl will reject malformed URLs (empty host)
    with pytest.raises(ValueError, match="Invalid openai_base_url.*Must be a valid HTTP or HTTPS URL"):
        Config(openai_base_url="https://")


def test_openai_base_url_validation_valid_http() -> None:
    """Test that http:// URLs are accepted"""
    config = Config(openai_base_url="http://localhost:8080/v1")
    assert config.openai_base_url == "http://localhost:8080/v1"


def test_openai_base_url_validation_valid_https() -> None:
    """Test that https:// URLs are accepted"""
    config = Config(openai_base_url="https://us.api.openai.com/v1")
    assert config.openai_base_url == "https://us.api.openai.com/v1"


def test_openai_base_wss_url_validation_invalid_protocol() -> None:
    """Test that openai_base_wss_url must start with ws:// or wss://"""
    with pytest.raises(ValueError, match="Must start with 'ws://' or 'wss://'"):
        Config(openai_base_wss_url="https://invalid.com")


def test_openai_base_wss_url_validation_malformed() -> None:
    """Test that openai_base_wss_url must be well-formed"""
    with pytest.raises(ValueError, match="Must include a valid domain after the protocol"):
        Config(openai_base_wss_url="wss://")


def test_openai_base_wss_url_validation_valid_ws() -> None:
    """Test that ws:// URLs are accepted"""
    config = Config(openai_base_wss_url="ws://localhost:8080/v1/realtime")
    assert config.openai_base_wss_url == "ws://localhost:8080/v1/realtime"


def test_openai_base_wss_url_validation_valid_wss() -> None:
    """Test that wss:// URLs are accepted"""
    config = Config(openai_base_wss_url="wss://us.api.openai.com/v1/realtime")
    assert config.openai_base_wss_url == "wss://us.api.openai.com/v1/realtime"


def test_empty_openai_base_url_is_allowed() -> None:
    """Test that empty strings are accepted (treated as unset)"""
    config = Config(openai_base_url="", openai_base_wss_url="")
    assert config.openai_base_url == ""
    assert config.openai_base_wss_url == ""
