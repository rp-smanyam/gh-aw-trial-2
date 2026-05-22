"""Unit tests for voice text normalizer functions."""

import pytest

from agent_leasing.util.voice_text_normalizer import (
    normalize_currency,
    normalize_date,
    normalize_field_value,
    normalize_id,
    normalize_json_values,
    normalize_number,
    normalize_phone,
    voice_text_normalize,
)


class TestNormalizeCurrency:
    """Test cases for normalize_currency."""

    @pytest.mark.parametrize(
        "input_val,expected",
        [
            ("$123.45", "one hundred and twenty-three dollars, forty-five cents"),
            ("$0.00", "zero dollars, zero cents"),
            ("$1,899.92", "one thousand, eight hundred and ninety-nine dollars, ninety-two cents"),
            ("$25.00", "twenty-five dollars, zero cents"),
            ("$1,000.00", "one thousand dollars, zero cents"),
            ("$0.99", "zero dollars, ninety-nine cents"),
        ],
    )
    def test_normalize_currency(self, input_val, expected):
        assert normalize_currency(input_val) == expected

    def test_currency_in_sentence(self):
        result = normalize_currency("Your balance is $123.45 today")
        assert "one hundred and twenty-three dollars" in result
        assert "forty-five cents" in result

    @pytest.mark.parametrize(
        "input_val,expected",
        [
            ("($79.11)", "negative seventy-nine dollars, eleven cents"),
            ("-$79.11", "negative seventy-nine dollars, eleven cents"),
            (
                "($1,234.56)",
                "negative one thousand, two hundred and thirty-four dollars, fifty-six cents",
            ),
        ],
    )
    def test_normalize_negative_balance(self, input_val, expected):
        """Parenthesized or negative currency must preserve the negative sign."""
        assert normalize_currency(input_val) == expected

    def test_parenthesized_negative_in_sentence(self):
        assert (
            normalize_currency("Your balance is ($79.11) today")
            == "Your balance is negative seventy-nine dollars, eleven cents today"
        )

    def test_negative_prefix_in_sentence(self):
        assert (
            normalize_currency("Your balance is -$79.11 today")
            == "Your balance is negative seventy-nine dollars, eleven cents today"
        )

    def test_no_currency_passthrough(self):
        assert normalize_currency("no money here") == "no money here"


class TestNormalizePhone:
    """Test cases for normalize_phone."""

    def test_e164_phone(self):
        result = normalize_phone("+15103810111")
        assert result == "plus one five one zero three eight one zero one one one"

    def test_911_special_case(self):
        assert normalize_phone("911") == "nine one one"

    def test_standard_us_phone(self):
        result = normalize_phone("(510) 381-0111")
        assert "five one zero three eight one zero one one one" in result

    def test_non_phone_passthrough(self):
        assert normalize_phone("hello") == "hello"


class TestNormalizeDate:
    """Test cases for normalize_date."""

    def test_iso_date(self):
        result = normalize_date("2025-10-13")
        assert result == "October thirteenth, twenty twenty-five"

    def test_iso_datetime(self):
        result = normalize_date("2025-12-25T12:00:00-08:00")
        assert result == "December twenty fifth, twenty twenty-five"

    def test_us_format_date(self):
        result = normalize_date("05/09/2025")
        assert result == "May ninth, twenty twenty-five"

    def test_us_format_single_digit(self):
        result = normalize_date("1/1/2025")
        assert result == "January first, twenty twenty-five"

    def test_no_date_passthrough(self):
        assert normalize_date("not a date") == "not a date"

    def test_year_2000(self):
        result = normalize_date("2000-01-01")
        assert "January" in result
        assert "first" in result

    def test_year_2005(self):
        result = normalize_date("2005-06-15")
        assert "June" in result
        assert "fifteenth" in result
        assert "twenty oh five" in result


class TestNormalizeId:
    """Test cases for normalize_id."""

    def test_sr_id(self):
        result = normalize_id("SR 1234-5")
        assert result == "S R one two three four dash five"

    def test_ref_code(self):
        result = normalize_id("REF 9876")
        assert result == "R E F nine eight seven six"

    def test_digit_dash(self):
        result = normalize_id("1234-5678")
        assert result == "one two three four dash five six seven eight"

    def test_alphanumeric(self):
        result = normalize_id("PKG-A1B2")
        assert "P" in result
        assert "K" in result
        assert "G" in result


class TestNormalizeNumber:
    """Test cases for normalize_number."""

    def test_small_number_word_form(self):
        result = normalize_number("123")
        assert result == "one hundred and twenty-three"

    def test_decimal_number(self):
        result = normalize_number("2.5")
        assert result == "two point five"

    def test_large_number_digit_by_digit(self):
        result = normalize_number("123456")
        # Large numbers should be digit-by-digit
        assert "one" in result
        assert "two" in result

    def test_comma_separated(self):
        result = normalize_number("1,500")
        assert "one thousand, five hundred" in result

    def test_no_number_passthrough(self):
        assert normalize_number("no numbers") == "no numbers"


class TestNormalizeFieldValue:
    """Test cases for the explicit field-name-based dispatcher.

    Field names tested here correspond to actual MCP tool response fields
    (see tests/stubbed_mcp.py).
    """

    def test_mapped_currency_field_old_format(self):
        result = normalize_field_value("$500.00", field_name="balance")
        assert "five hundred dollars" in result

    def test_mapped_currency_field_new_format(self):
        result = normalize_field_value("$123.45", field_name="current_balance")
        assert "dollars" in result

    def test_mapped_date_field(self):
        result = normalize_field_value("2025-09-01", field_name="rent_due_date")
        assert "September" in result

    def test_mapped_id_field(self):
        result = normalize_field_value("5265-1", field_name="sr_id")
        assert "five two six five dash one" in result

    def test_mapped_tracking_number(self):
        result = normalize_field_value("123456789", field_name="trackingNumber")
        assert "one two three" in result

    def test_mapped_event_date(self):
        result = normalize_field_value("2025-07-21T21:00:00-07:00", field_name="startDate")
        assert "July" in result

    def test_unmapped_field_passes_through(self):
        """Fields not in FIELD_NORMALIZATIONS should NOT be normalized."""
        assert normalize_field_value("$123.45", field_name="description") == "$123.45"
        assert normalize_field_value("+15103810111", field_name="notes") == "+15103810111"
        assert normalize_field_value("2025-10-13", field_name="summary") == "2025-10-13"
        assert normalize_field_value("SR 1234-5", field_name="title") == "SR 1234-5"

    def test_no_field_name_passes_through(self):
        """Without a field name, nothing is normalized."""
        assert normalize_field_value("$123.45") == "$123.45"
        assert normalize_field_value("+15103810111") == "+15103810111"
        assert normalize_field_value("2025-10-13") == "2025-10-13"

    def test_empty_string_passthrough(self):
        assert normalize_field_value("") == ""
        assert normalize_field_value("  ") == "  "

    def test_non_string_passthrough(self):
        assert normalize_field_value(None) is None  # type: ignore[arg-type]

    def test_case_insensitive_lookup(self):
        """Field names should match case-insensitively."""
        result = normalize_field_value("$100.00", field_name="Balance")
        assert "dollars" in result


class TestNormalizeJsonValues:
    """Test cases for recursive JSON normalization."""

    def test_simple_dict(self):
        data = {"balance": "$100.00", "name": "John"}
        result = normalize_json_values(data)
        assert "dollars" in result["balance"]
        assert result["name"] == "John"

    def test_nested_dict(self):
        data = {"result": {"rent_due_date": "2025-09-01", "balance": "$123.45"}}
        result = normalize_json_values(data)
        assert "September" in result["result"]["rent_due_date"]
        assert "dollars" in result["result"]["balance"]

    def test_list_values_unmapped_field(self):
        data = {"amounts": ["$10.00", "$20.00"]}
        result = normalize_json_values(data)
        assert result["amounts"][0] == "$10.00"
        assert result["amounts"][1] == "$20.00"

    def test_preserves_non_strings(self):
        data = {"count": 5, "active": True, "data": None}
        result = normalize_json_values(data)
        assert result["count"] == 5
        assert result["active"] is True
        assert result["data"] is None

    def test_preserves_keys(self):
        data = {"$100.00": "value", "2025-10-13": "date_value"}
        result = normalize_json_values(data)
        assert "$100.00" in result
        assert "2025-10-13" in result

    def test_rent_information_old_format(self):
        data = {
            "result": {
                "balance": "$123.45",
                "pending_balance": "$0.00",
                "rent": "$1,899.00",
                "rent_due_date": "2025-09-01",
                "total_balance_due": "$123.45",
            }
        }
        result = normalize_json_values(data)
        assert "dollars" in result["result"]["balance"]
        assert "September" in result["result"]["rent_due_date"]
        assert "dollars" in result["result"]["rent"]

    def test_rent_information_new_format(self):
        data = {
            "current_balance": "$123.45",
            "past_due_balance": "$0.00",
            "rent": "$1,899.00",
            "rent_due_date": "2026-01-06T00:00:00+00:00",
        }
        result = normalize_json_values(data)
        assert "dollars" in result["current_balance"]
        assert "dollars" in result["rent"]
        assert "January" in result["rent_due_date"]

    def test_negative_balance_new_format(self):
        """Parenthesized negative balances must normalize through JSON path."""
        data = {
            "current_balance": "($79.11)",
            "past_due_balance": "$0.00",
            "rent": "$1,899.00",
        }
        result = normalize_json_values(data)
        assert result["current_balance"] == "negative seventy-nine dollars, eleven cents"
        assert result["past_due_balance"] == "zero dollars, zero cents"
        assert result["rent"] == "one thousand, eight hundred and ninety-nine dollars, zero cents"

    def test_negative_balance_old_format(self):
        data = {"result": {"balance": "($79.11)", "rent": "$1,899.00"}}
        result = normalize_json_values(data)
        assert result["result"]["balance"] == "negative seventy-nine dollars, eleven cents"
        assert result["result"]["rent"] == "one thousand, eight hundred and ninety-nine dollars, zero cents"


class TestVoiceTextNormalize:
    """Test cases for the safety-net free-text normalizer."""

    def test_currency_in_text(self):
        result = voice_text_normalize("Your rent is $1,899.00.")
        assert "one thousand, eight hundred and ninety-nine dollars" in result

    def test_date_in_text(self):
        result = voice_text_normalize("Rent is due on 2025-09-01.")
        assert "September first" in result

    def test_us_date_in_text(self):
        result = voice_text_normalize("Date: 05/09/2025")
        assert "May ninth" in result

    def test_phone_in_text(self):
        result = voice_text_normalize("Call +15103810111 for help.")
        assert "plus one five one zero" in result

    def test_id_in_text(self):
        result = voice_text_normalize("Your request is SR 1234-5.")
        assert "S R" in result
        assert "one two three four dash five" in result

    def test_no_change_for_plain_text(self):
        text = "Hello, how can I help you?"
        assert voice_text_normalize(text) == text

    def test_empty_string(self):
        assert voice_text_normalize("") == ""

    def test_none_passthrough(self):
        assert voice_text_normalize(None) is None  # type: ignore[arg-type]

    def test_credit_context_in_text(self):
        assert (
            voice_text_normalize("You have a credit of ($79.11) on your account.")
            == "You have a credit of negative seventy-nine dollars, eleven cents on your account."
        )

    def test_negative_currency_in_text(self):
        assert (
            voice_text_normalize("Your balance is -$79.11.")
            == "Your balance is negative seventy-nine dollars, eleven cents."
        )

    def test_mixed_content(self):
        text = "Your balance of $123.45 is due on 2025-10-13. Call +15103810111 for SR 1234-5."
        result = voice_text_normalize(text)
        assert "dollars" in result
        assert "October" in result
        assert "plus" in result
        assert "S R" in result
