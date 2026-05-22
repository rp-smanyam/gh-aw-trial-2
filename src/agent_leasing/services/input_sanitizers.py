"""
Input sanitization pipeline for cleaning user input before it reaches the LLM.

This module provides a generic, extensible framework for sanitizing user input.
Each sanitizer is a function that takes a string and returns a tuple of
(sanitized_string, bool) where the bool indicates if any changes were made.

Usage:
    from agent_leasing.services.input_sanitizers import sanitize_input

    cleaned_prompt = sanitize_input(user_prompt)
"""

import re
from typing import Callable

import structlog
from urlextract import URLExtract

logger = structlog.getLogger(__name__)

# Type alias for sanitizer functions
# Each sanitizer takes a string and returns (sanitized_string, was_modified)
Sanitizer = Callable[[str], tuple[str, bool]]


# =============================================================================
# Individual Sanitizers
# =============================================================================

URL_REPLACEMENT = "[external link removed]"

_url_extractor = URLExtract()

# Pattern that matches malformed regex character classes containing shorthand sequences
# e.g., [\w (unclosed character class) that cause parsing_exception in ESQL/SQL REGEXP queries
MALFORMED_REGEX_CHAR_CLASS_PATTERN = re.compile(r"\[\\[wWdDsS](?!\])")
MALFORMED_REGEX_REPLACEMENT = "[input removed]"

# Pattern that matches camelCase identifiers which could be misinterpreted as
# ESQL column references (e.g., errorType, filePath, functionName, lineNumber).
# Targets identifiers with common technical suffixes that appear in error metadata
# but are unlikely to appear legitimately in resident-facing conversations.
CAMEL_CASE_COLUMN_PATTERN = re.compile(r"\b[a-zA-Z]+(?:Type|Path|Name|Number|Id|Code|Message)\b")

ESQL_COLUMN_REPLACEMENT = "[value removed]"


def sanitize_esql_column_references(text: str) -> tuple[str, bool]:
    """
    Replace camelCase identifiers that could be misinterpreted as ESQL column references.

    ESQL (Elasticsearch Query Language) can treat unquoted camelCase identifiers
    such as ``errorType``, ``filePath``, ``functionName``, and ``lineNumber`` as
    column references.  When those columns do not exist in the target index a
    ``verification_exception`` is raised.  This sanitizer removes such identifiers
    from user input before they can reach query execution.

    Args:
        text: The input text to sanitize

    Returns:
        Tuple of (sanitized_text, was_modified)

    Examples:
        >>> sanitize_esql_column_references("errorType: 500")
        ('[value removed]: 500', True)

        >>> sanitize_esql_column_references("plain text no camel case")
        ('plain text no camel case', False)
    """
    if not text:
        return text, False

    matches = CAMEL_CASE_COLUMN_PATTERN.findall(text)
    if not matches:
        return text, False

    sanitized = CAMEL_CASE_COLUMN_PATTERN.sub(ESQL_COLUMN_REPLACEMENT, text)
    logger.info(f"Sanitized {len(matches)} potential ESQL column reference(s) from input")
    return sanitized, True


def sanitize_urls(text: str) -> tuple[str, bool]:
    """
    Replace URLs with a placeholder to prevent external link exposure.

    Args:
        text: The input text to sanitize

    Returns:
        Tuple of (sanitized_text, was_modified)

    Examples:
        >>> sanitize_urls("Check out https://example.com")
        ('Check out [external link removed]', True)

        >>> sanitize_urls("No links here")
        ('No links here', False)
    """
    if not text:
        return text, False

    urls = list(_url_extractor.gen_urls(text))
    if not urls:
        return text, False

    sanitized = text
    for url in urls:
        sanitized = sanitized.replace(url, URL_REPLACEMENT)

    logger.info(f"Sanitized {len(urls)} URL(s) from input")
    return sanitized, True


def sanitize_regex_patterns(text: str) -> tuple[str, bool]:
    """
    Replace malformed regex character class patterns to prevent parsing exceptions.

    Detects patterns like ``[\\w`` (an unclosed character class containing a regex
    shorthand such as ``\\w``, ``\\d``, ``\\s``, etc.) that cause
    ``parsing_exception`` errors when passed to downstream SQL/ESQL REGEXP
    query functions.

    Args:
        text: The input text to sanitize

    Returns:
        Tuple of (sanitized_text, was_modified)

    Examples:
        >>> sanitize_regex_patterns("search for [\\\\w+ patterns")
        ('search for [input removed]+ patterns', True)

        >>> sanitize_regex_patterns("Normal text without regex")
        ('Normal text without regex', False)
    """
    if not text:
        return text, False

    matches = MALFORMED_REGEX_CHAR_CLASS_PATTERN.findall(text)
    if not matches:
        return text, False

    sanitized = MALFORMED_REGEX_CHAR_CLASS_PATTERN.sub(MALFORMED_REGEX_REPLACEMENT, text)
    logger.info(f"Sanitized {len(matches)} malformed regex pattern(s) from input")
    return sanitized, True


# =============================================================================
# Sanitization Pipeline
# =============================================================================

# List of sanitizers to apply in order
# Add new sanitizers here as they are developed
DEFAULT_SANITIZERS: list[Sanitizer] = [
    sanitize_urls,
    sanitize_regex_patterns,
    sanitize_esql_column_references,
]


def sanitize_input(text: str, sanitizers: list[Sanitizer] | None = None) -> str:
    """
    Apply all sanitizers to the input text.

    This function runs each sanitizer in sequence, passing the output of one
    as the input to the next. This allows sanitizers to be composed together.

    Args:
        text: The input text to sanitize
        sanitizers: Optional list of sanitizer functions to apply.
                   Defaults to DEFAULT_SANITIZERS.

    Returns:
        The sanitized text with all transformations applied

    Examples:
        >>> sanitize_input("Visit https://sketchy.com for more")
        'Visit [external link removed] for more'

        >>> sanitize_input("Hello world")
        'Hello world'
    """
    if not text:
        return text

    sanitizers_to_apply = sanitizers if sanitizers is not None else DEFAULT_SANITIZERS

    for sanitizer in sanitizers_to_apply:
        text, _ = sanitizer(text)

    return text
