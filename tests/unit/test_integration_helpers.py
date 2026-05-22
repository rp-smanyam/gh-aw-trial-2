import pytest

from tests.integration.helpers import patch_context


class _DummyProductInfo:
    def __init__(self) -> None:
        self.dispatch_schedule_active = None


class _DummyAskRequest:
    def __init__(self) -> None:
        self.product_info = _DummyProductInfo()


class _DummyContext:
    def __init__(self) -> None:
        self.ask_request = _DummyAskRequest()
        self.settings = {"mode": "default"}


def test_patch_context_applies_dotted_paths() -> None:
    context = _DummyContext()

    patched = patch_context(
        context,
        {
            "ask_request.product_info.dispatch_schedule_active": "AI Maintenance",
            "settings.mode": "patched",
        },
    )

    assert patched is context
    assert context.ask_request.product_info.dispatch_schedule_active == "AI Maintenance"
    assert context.settings["mode"] == "patched"


def test_patch_context_no_config_returns_context() -> None:
    context = _DummyContext()

    patched = patch_context(context, None)

    assert patched is context
    assert context.ask_request.product_info.dispatch_schedule_active is None
    assert context.settings["mode"] == "default"


def test_patch_context_rejects_empty_path() -> None:
    context = _DummyContext()

    with pytest.raises(ValueError, match="test_config key must be a non-empty string"):
        patch_context(context, {"": "value"})
