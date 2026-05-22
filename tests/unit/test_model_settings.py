import pytest
from agents import Agent, ModelSettings

from agent_leasing.settings import Config, build_model_settings, settings


@pytest.mark.parametrize(
    ("model", "effort", "verbosity", "service_tier"),
    [
        ("gpt-4.1-mini", "medium", "low", "priority"),
        ("gpt-5-mini", "medium", "low", "priority"),
        ("gpt-5-mini", "medium", "medium", "priority"),
        ("gpt-5.1", "medium", "low", "priority"),
        ("gpt-5.1", "none", "medium", "priority"),
        ("gpt-5-nano", "minimal", "low", "priority"),
        ("gpt-5.1", "none", "low", "priority"),
    ],
)
def test_agent_model_settings(model, effort, verbosity, service_tier):
    model_settings = build_model_settings(
        model=model,
        effort=effort,
        verbosity=verbosity,
        temperature=0.7,
        service_tier=service_tier,
    )
    expected = ModelSettings(
        reasoning={"effort": effort},
        extra_body={"text": {"verbosity": verbosity}},
        extra_args={"service_tier": service_tier},
        verbosity=verbosity,
    )

    if model.startswith("gpt-5"):
        assert expected.reasoning.effort == model_settings.reasoning.effort
    assert expected.verbosity == model_settings.verbosity
    assert expected.extra_args["service_tier"] == model_settings.extra_args["service_tier"]


@pytest.mark.parametrize(
    ("model", "effort"),
    [
        ("gpt-4.1-mini", "medium"),
        ("gpt-5-mini", "medium"),
        ("gpt-5.1", "medium"),
        ("gpt-5-nano", "minimal"),
        ("gpt-5.1", "none"),
    ],
)
def test_agent_instantiation_with_supported_models(model, effort):
    model_settings = build_model_settings(
        model=model,
        effort=effort,
        verbosity="medium",
        temperature=0.7,
        service_tier="priority",
    )

    agent = Agent(
        name="Model Smoke Test Agent",
        instructions="Return a polite greeting.",
        model=model,
        model_settings=model_settings,
    )

    assert agent.model == model
    assert agent.model_settings is model_settings


@pytest.mark.parametrize(
    ("channel", "expire"),
    [
        ("sms", "10m"),
        ("email", "20m"),
        ("chat", "30m"),
        ("voice", "40m"),
        ("", "5m"),
        (None, "5m"),
    ],
)
def test_cache_expiration_per_channel(channel, expire):
    """cache_expiration should return the channel-specific expiration values."""
    settings.expire_default = "5m"
    settings.expire_sms = "10m"
    settings.expire_email = "20m"
    settings.expire_chat = "30m"
    settings.expire_voice = "40m"
    assert settings.cache_expiration(channel) == expire


@pytest.mark.parametrize(
    ("openai_base_wss_url", "realtime_model", "expected"),
    [
        (
            "",
            "gpt-realtime-2",
            "wss://api.openai.com/v1/realtime?model=gpt-realtime-2",
        ),
        (
            "wss://example.com/v1/realtime",
            "gpt-realtime-2",
            "wss://example.com/v1/realtime?model=gpt-realtime-2",
        ),
        (
            "wss://example.com/realtime",
            "gpt-realtime-mini",
            "wss://example.com/realtime?model=gpt-realtime-mini",
        ),
    ],
)
def test_openai_wss_full_endpoint(openai_base_wss_url, realtime_model, expected):
    config = Config(
        openai_base_wss_url=openai_base_wss_url,
        realtime_model=realtime_model,
    )
    assert config.openai_wss_full_endpoint == expected
