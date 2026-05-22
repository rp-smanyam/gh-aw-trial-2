"""
Utility module for conditional LangSmith integration in tests.

This module provides decorators and logging functions that are only active
when LangSmith is properly configured via environment variables.
"""

from typing import Any, Dict, Optional

import pytest
from langsmith import testing as langsmith_testing

from agent_leasing.settings import settings


def is_langsmith_enabled():
    """Return true if LangSmith tracing is enabled and properly configured."""
    if not settings.langsmith_tracing:
        return False

    if not is_langsmith_configured():
        return False

    return True


def is_langsmith_configured():
    """Checks if the langsmith connection is properly configured"""
    return settings.langsmith_endpoint and settings.langsmith_api_key


def conditional_langsmith_test_decorator(*test_decorators):
    """
    Decorator factory that conditionally applies LangSmith test decorators.

    Args:
        *test_decorators: Additional decorators to always apply (like parametrize)

    Returns:
        A decorator function that applies both standard and LangSmith decorators
    """

    def decorator(func):
        langsmith_enabled = is_langsmith_enabled()

        for test_decorator in reversed(test_decorators):
            func = test_decorator(func)

        # Only apply LangSmith decorators if enabled
        if langsmith_enabled:
            func = pytest.mark.langsmith(func)
            func = pytest.mark.skipif(
                not is_langsmith_configured(),
                reason="Skipping because LangSmith endpoint or API key is not properly configured.",
            )(func)

        return func

    return decorator


def log_test_data(
    inputs: Optional[Dict[str, Any]] = None,
    reference_outputs: Optional[Dict[str, Any]] = None,
    outputs: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Conditionally log test data to LangSmith if enabled.

    Args:
        inputs: Input data to log
        reference_outputs: Expected/reference output data to log
        outputs: Actual output data to log
    """
    langsmith_enabled = is_langsmith_enabled()
    if not langsmith_enabled:
        return

    if inputs:
        langsmith_testing.log_inputs(inputs)

    if reference_outputs:
        langsmith_testing.log_reference_outputs(reference_outputs)

    if outputs:
        langsmith_testing.log_outputs(outputs)
