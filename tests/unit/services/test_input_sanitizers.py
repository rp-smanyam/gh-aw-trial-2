"""Tests for input sanitizers."""

from agent_leasing.services.input_sanitizers import (
    DEFAULT_SANITIZERS,
    ESQL_COLUMN_REPLACEMENT,
    MALFORMED_REGEX_REPLACEMENT,
    URL_REPLACEMENT,
    sanitize_esql_column_references,
    sanitize_input,
    sanitize_regex_patterns,
    sanitize_urls,
)


class TestSanitizeUrls:
    """Tests for the URL sanitizer."""

    def test_sanitize_http_url(self):
        """Test that http:// URLs are sanitized."""
        text = "Check out http://example.com for more info"
        result, modified = sanitize_urls(text)

        assert modified is True
        assert "http://example.com" not in result
        assert URL_REPLACEMENT in result
        assert result == f"Check out {URL_REPLACEMENT} for more info"

    def test_sanitize_https_url(self):
        """Test that https:// URLs are sanitized."""
        text = "Visit https://secure-site.com/path/to/page"
        result, modified = sanitize_urls(text)

        assert modified is True
        assert "https://secure-site.com" not in result
        assert URL_REPLACEMENT in result

    def test_sanitize_www_url(self):
        """Test that www. URLs are sanitized."""
        text = "Go to www.example.com for details"
        result, modified = sanitize_urls(text)

        assert modified is True
        assert "www.example.com" not in result
        assert URL_REPLACEMENT in result

    def test_sanitize_multiple_urls(self):
        """Test that multiple URLs are all sanitized."""
        text = "Check https://site1.com and http://site2.com and www.site3.com"
        result, modified = sanitize_urls(text)

        assert modified is True
        assert "site1.com" not in result
        assert "site2.com" not in result
        assert "site3.com" not in result
        assert result.count(URL_REPLACEMENT) == 3

    def test_bare_domain_mid_sentence(self):
        """Test that a bare domain in the middle of a sentence is sanitized."""
        text = "I saw your listing on apartments.com and wanted to ask about it"
        result, modified = sanitize_urls(text)

        assert modified is True
        assert "apartments.com" not in result
        assert URL_REPLACEMENT in result
        assert result == f"I saw your listing on {URL_REPLACEMENT} and wanted to ask about it"

    def test_mixed_url_types_mid_sentence(self):
        """Test that multiple different URL formats embedded in natural text are all sanitized."""
        text = (
            "Hey I found the place on zillow.com, my agent also sent me https://apartments.com/listing/123 "
            "and told me to check www.redfin.com/homes for comparisons, hope that helps!"
        )
        result, modified = sanitize_urls(text)

        assert modified is True
        assert "zillow.com" not in result
        assert "https://apartments.com/listing/123" not in result
        assert "www.redfin.com/homes" not in result
        assert result.count(URL_REPLACEMENT) == 3
        assert result.startswith("Hey I found the place on")
        assert result.endswith("hope that helps!")

    def test_subdomain_url(self):
        """Test that subdomains are sanitized."""
        text = "Check out listings.apartments.com for details"
        result, modified = sanitize_urls(text)

        assert modified is True
        assert "listings.apartments.com" not in result
        assert URL_REPLACEMENT in result

    def test_email_not_sanitized(self):
        """Test that email addresses are not treated as URLs."""
        text = "You can reach me at bob@gmail.com anytime"
        result, modified = sanitize_urls(text)

        assert modified is False
        assert "bob@gmail.com" in result

    def test_file_extension_not_sanitized(self):
        """Test that file extensions like .pdf and .txt are not treated as URLs."""
        text = "I attached my resume.pdf and notes.txt to the application"
        result, modified = sanitize_urls(text)

        assert modified is False
        assert result == text

    def test_street_address_not_sanitized(self):
        """Test that street addresses are not treated as URLs."""
        text = "I live at 123 Main St. and want to apply for unit 2B"
        result, modified = sanitize_urls(text)

        assert modified is False
        assert result == text

    def test_sanitize_url_with_path_and_query(self):
        """Test URLs with paths and query parameters."""
        text = "See https://example.com/path/to/page?query=value&other=123"
        result, modified = sanitize_urls(text)

        assert modified is True
        assert "example.com" not in result
        assert "query=value" not in result

    def test_no_urls_returns_unmodified(self):
        """Test that text without URLs is returned unchanged."""
        text = "This is just plain text without any links"
        result, modified = sanitize_urls(text)

        assert modified is False
        assert result == text

    def test_empty_string(self):
        """Test that empty string is handled."""
        result, modified = sanitize_urls("")

        assert modified is False
        assert result == ""

    def test_none_input(self):
        """Test that None input is handled."""
        result, modified = sanitize_urls(None)

        assert modified is False
        assert result is None

    def test_url_at_start_of_text(self):
        """Test URL at the beginning of text."""
        text = "https://example.com is a great site"
        result, modified = sanitize_urls(text)

        assert modified is True
        assert result.startswith(URL_REPLACEMENT)

    def test_url_at_end_of_text(self):
        """Test URL at the end of text."""
        text = "Visit the site at https://example.com"
        result, modified = sanitize_urls(text)

        assert modified is True
        assert result.endswith(URL_REPLACEMENT)

    def test_url_only(self):
        """Test input that is only a URL."""
        text = "https://example.com"
        result, modified = sanitize_urls(text)

        assert modified is True
        assert result == URL_REPLACEMENT

    def test_case_insensitive(self):
        """Test that URL matching is case insensitive."""
        text = "Check HTTPS://EXAMPLE.COM and HTTP://test.com"
        result, modified = sanitize_urls(text)

        assert modified is True
        assert "HTTPS://EXAMPLE.COM" not in result
        assert "HTTP://test.com" not in result

    def test_preserves_surrounding_text(self):
        """Test that text around URLs is preserved."""
        text = "Before https://example.com after"
        result, modified = sanitize_urls(text)

        assert modified is True
        assert result == f"Before {URL_REPLACEMENT} after"

    def test_url_with_port(self):
        """Test URL with port number."""
        text = "Connect to https://localhost:8080/api"
        result, modified = sanitize_urls(text)

        assert modified is True
        assert "localhost:8080" not in result

    def test_complex_url(self):
        """Test complex URL with various components."""
        text = "API at https://api.example.com:443/v1/users?id=123&token=abc#section"
        result, modified = sanitize_urls(text)

        assert modified is True
        assert "api.example.com" not in result


class TestSanitizeInput:
    """Tests for the main sanitize_input function."""

    def test_applies_default_sanitizers(self):
        """Test that default sanitizers are applied."""
        text = "Check https://example.com for info"
        result = sanitize_input(text)

        assert "https://example.com" not in result
        assert URL_REPLACEMENT in result

    def test_empty_string(self):
        """Test empty string handling."""
        result = sanitize_input("")
        assert result == ""

    def test_none_input(self):
        """Test None input handling."""
        result = sanitize_input(None)
        assert result is None

    def test_text_without_sensitive_content(self):
        """Test that clean text passes through unchanged."""
        text = "Hello, I have a question about my rent payment."
        result = sanitize_input(text)
        assert result == text

    def test_custom_sanitizers(self):
        """Test that custom sanitizers can be provided."""

        def custom_sanitizer(text: str) -> tuple[str, bool]:
            if "secret" in text.lower():
                return text.replace("secret", "[REDACTED]"), True
            return text, False

        text = "The secret code is 1234"
        result = sanitize_input(text, sanitizers=[custom_sanitizer])

        assert "secret" not in result
        assert "[REDACTED]" in result

    def test_empty_sanitizers_list(self):
        """Test that empty sanitizers list returns original text."""
        text = "Check https://example.com"
        result = sanitize_input(text, sanitizers=[])

        assert result == text  # No sanitization applied

    def test_multiple_sanitizers_chain(self):
        """Test that multiple sanitizers are applied in sequence."""

        def add_prefix(text: str) -> tuple[str, bool]:
            return f"[SANITIZED] {text}", True

        text = "Visit https://example.com"
        result = sanitize_input(text, sanitizers=[sanitize_urls, add_prefix])

        assert URL_REPLACEMENT in result
        assert result.startswith("[SANITIZED]")

    def test_default_sanitizers_list_not_empty(self):
        """Verify DEFAULT_SANITIZERS contains expected sanitizers."""
        assert len(DEFAULT_SANITIZERS) > 0
        assert sanitize_urls in DEFAULT_SANITIZERS
        assert sanitize_regex_patterns in DEFAULT_SANITIZERS
        assert sanitize_esql_column_references in DEFAULT_SANITIZERS


class TestSanitizeRegexPatterns:
    """Tests for the regex pattern sanitizer."""

    def test_sanitize_unclosed_word_class(self):
        """Test that [\\w (unclosed word character class) is sanitized."""
        text = r"search for [\w+ patterns"
        result, modified = sanitize_regex_patterns(text)

        assert modified is True
        assert r"[\w" not in result
        assert MALFORMED_REGEX_REPLACEMENT in result

    def test_sanitize_unclosed_digit_class(self):
        """Test that [\\d (unclosed digit character class) is sanitized."""
        text = r"match [\d+ digits"
        result, modified = sanitize_regex_patterns(text)

        assert modified is True
        assert r"[\d" not in result
        assert MALFORMED_REGEX_REPLACEMENT in result

    def test_sanitize_unclosed_space_class(self):
        """Test that [\\s (unclosed space character class) is sanitized."""
        text = r"whitespace [\s+ here"
        result, modified = sanitize_regex_patterns(text)

        assert modified is True
        assert r"[\s" not in result
        assert MALFORMED_REGEX_REPLACEMENT in result

    def test_sanitize_uppercase_variants(self):
        """Test that uppercase variants [\\W, [\\D, [\\S are sanitized."""
        for pattern in [r"[\W", r"[\D", r"[\S"]:
            text = f"input with {pattern} here"
            result, modified = sanitize_regex_patterns(text)

            assert modified is True, f"Expected modification for {pattern!r}"
            assert pattern not in result

    def test_properly_closed_class_not_sanitized(self):
        """Test that properly closed character classes like [\\w] are not sanitized."""
        text = r"valid pattern [\w] here"
        result, modified = sanitize_regex_patterns(text)

        assert modified is False
        assert result == text

    def test_no_regex_patterns_returns_unmodified(self):
        """Test that text without regex patterns is returned unchanged."""
        text = "This is just plain text without any regex"
        result, modified = sanitize_regex_patterns(text)

        assert modified is False
        assert result == text

    def test_empty_string(self):
        """Test that empty string is handled."""
        result, modified = sanitize_regex_patterns("")

        assert modified is False
        assert result == ""

    def test_none_input(self):
        """Test that None input is handled."""
        result, modified = sanitize_regex_patterns(None)

        assert modified is False
        assert result is None

    def test_multiple_malformed_patterns(self):
        r"""Test that multiple malformed patterns are all sanitized."""
        text = r"[\w and [\d are both malformed"
        result, modified = sanitize_regex_patterns(text)

        assert modified is True
        assert r"[\w" not in result
        assert r"[\d" not in result
        assert result.count(MALFORMED_REGEX_REPLACEMENT) == 2

    def test_preserves_surrounding_text(self):
        """Test that text around patterns is preserved."""
        text = r"Before [\w+ after"
        result, modified = sanitize_regex_patterns(text)

        assert modified is True
        assert "Before " in result
        assert " after" in result


class TestSanitizeEsqlColumnReferences:
    """Tests for the ESQL camelCase column reference sanitizer."""

    def test_sanitize_error_type(self):
        """Test that errorType is sanitized."""
        text = "errorType: 500"
        result, modified = sanitize_esql_column_references(text)

        assert modified is True
        assert "errorType" not in result
        assert ESQL_COLUMN_REPLACEMENT in result

    def test_sanitize_file_path(self):
        """Test that filePath is sanitized."""
        text = "filePath is /home/user/file.py"
        result, modified = sanitize_esql_column_references(text)

        assert modified is True
        assert "filePath" not in result
        assert ESQL_COLUMN_REPLACEMENT in result

    def test_sanitize_function_name(self):
        """Test that functionName is sanitized."""
        text = "functionName: main"
        result, modified = sanitize_esql_column_references(text)

        assert modified is True
        assert "functionName" not in result
        assert ESQL_COLUMN_REPLACEMENT in result

    def test_sanitize_line_number(self):
        """Test that lineNumber is sanitized."""
        text = "lineNumber 42"
        result, modified = sanitize_esql_column_references(text)

        assert modified is True
        assert "lineNumber" not in result
        assert ESQL_COLUMN_REPLACEMENT in result

    def test_sanitize_multiple_camel_case_identifiers(self):
        """Test that multiple camelCase identifiers are all sanitized."""
        text = "errorType: 500, filePath: /app/main.py, functionName: run, lineNumber: 42"
        result, modified = sanitize_esql_column_references(text)

        assert modified is True
        assert "errorType" not in result
        assert "filePath" not in result
        assert "functionName" not in result
        assert "lineNumber" not in result
        assert result.count(ESQL_COLUMN_REPLACEMENT) == 4

    def test_no_technical_identifiers_returns_unmodified(self):
        """Test that text without technical camelCase identifiers is returned unchanged."""
        text = "plain text without any technical identifiers"
        result, modified = sanitize_esql_column_references(text)

        assert modified is False
        assert result == text

    def test_empty_string(self):
        """Test that empty string is handled."""
        result, modified = sanitize_esql_column_references("")

        assert modified is False
        assert result == ""

    def test_none_input(self):
        """Test that None input is handled."""
        result, modified = sanitize_esql_column_references(None)

        assert modified is False
        assert result is None

    def test_preserves_surrounding_text(self):
        """Test that text around camelCase identifiers is preserved."""
        text = "The errorType was unexpected"
        result, modified = sanitize_esql_column_references(text)

        assert modified is True
        assert result == f"The {ESQL_COLUMN_REPLACEMENT} was unexpected"

    def test_other_technical_suffixes_matched(self):
        """Test that other technical suffix patterns are also sanitized."""
        text = "statusCode: 404, requestId: abc123, errorMessage: not found"
        result, modified = sanitize_esql_column_references(text)

        assert modified is True
        assert "statusCode" not in result
        assert "requestId" not in result
        assert "errorMessage" not in result

    def test_plain_words_not_matched(self):
        """Test that plain words without technical suffixes are not matched."""
        text = "hello world rent payment"
        result, modified = sanitize_esql_column_references(text)

        assert modified is False
        assert result == text

    def test_common_words_ending_with_suffix_words_not_matched(self):
        """Test that standalone suffix words and plain sentences are not matched."""
        cases = [
            "What type of lease do you have?",
            "Please enter your name",
            "There was an error message",
            "The status code was 404",
        ]
        for text in cases:
            result, modified = sanitize_esql_column_references(text)
            assert modified is False, f"Expected no modification for: {repr(text)}"
            assert result == text


class TestRealWorldScenarios:
    """Tests for real-world input scenarios."""

    def test_apartment_listing_with_url(self):
        """Test the example from the requirements."""
        text = "Check out this apartment at https://sketchy-site.com"
        result = sanitize_input(text)

        assert result == f"Check out this apartment at {URL_REPLACEMENT}"

    def test_mixed_content(self):
        """Test message with URLs mixed with normal content."""
        text = (
            "Hi, I saw your listing on https://apartments.com and wanted to ask "
            "about the unit at 123 Main St. Can you also check www.reviews.com?"
        )
        result = sanitize_input(text)

        assert "https://apartments.com" not in result
        assert "www.reviews.com" not in result
        assert "123 Main St" in result  # Address preserved
        assert result.count(URL_REPLACEMENT) == 2

    def test_email_not_treated_as_url(self):
        """Test that email addresses are not sanitized as URLs."""
        text = "Contact me at user@example.com for more info"
        result = sanitize_input(text)

        assert "user@example.com" in result  # Email should be preserved

    def test_normal_resident_query(self):
        """Test typical resident query without URLs."""
        text = "When is my rent due? I need to make a payment."
        result = sanitize_input(text)

        assert result == text  # Unchanged
