import importlib
import os
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _clean_env():
    """Ensure OPENAI_BASE_URL is absent before/after each test."""
    old = os.environ.pop("OPENAI_BASE_URL", None)
    yield
    if old is not None:
        os.environ["OPENAI_BASE_URL"] = old
    else:
        os.environ.pop("OPENAI_BASE_URL", None)


def _reload_client_module():
    """Re-import the module so module-level code re-executes."""
    import agent_leasing.clients.openai as mod

    return importlib.reload(mod)


def test_empty_openai_base_url_env_var_is_removed():
    """When OPENAI_BASE_URL is set to an empty string the module should
    remove it from the environment so the OpenAI SDK doesn't use it as
    an invalid base URL.
    """
    os.environ["OPENAI_BASE_URL"] = ""

    with patch("agent_leasing.clients.openai.set_default_openai_client"):
        _reload_client_module()

    assert "OPENAI_BASE_URL" not in os.environ


def test_non_empty_openai_base_url_env_var_is_preserved():
    """A real OPENAI_BASE_URL value must not be removed."""
    os.environ["OPENAI_BASE_URL"] = "https://custom.openai.example.com/v1"

    with patch("agent_leasing.clients.openai.set_default_openai_client"):
        _reload_client_module()

    assert os.environ["OPENAI_BASE_URL"] == "https://custom.openai.example.com/v1"


def test_unset_openai_base_url_env_var_no_error():
    """When OPENAI_BASE_URL is not set at all the module should load without error."""
    os.environ.pop("OPENAI_BASE_URL", None)

    with patch("agent_leasing.clients.openai.set_default_openai_client"):
        _reload_client_module()

    assert "OPENAI_BASE_URL" not in os.environ
