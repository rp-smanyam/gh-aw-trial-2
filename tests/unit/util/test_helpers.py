import base64
from unittest.mock import patch

import pytest

from agent_leasing.util.helpers import (
    decode_object,
    encode_object,
    get_token_hash,
    humanize_numbers,
    resolve_greeting_placeholders,
)


class TestEncodeObject:
    # --- encode_object / decode_object ---
    def test_encode_decode_roundtrip(self):
        data = {"foo": "bar", "num": 123, "nested": {"a": [1, 2, 3]}}
        encoded = encode_object(data)
        # Should be base64 string
        assert isinstance(encoded, str)
        decoded = decode_object(encoded)
        assert decoded == data

    @pytest.mark.parametrize(
        "obj",
        [
            {},
            {"x": 1},
            {"list": [1, 2, 3]},
            {"nested": {"a": 1}},
        ],
    )
    def test_encode_decode_various(self, obj):
        assert decode_object(encode_object(obj)) == obj


class TestDecodeObject:
    # --- decode_object error handling ---
    def test_decode_object_invalid_base64(self):
        with pytest.raises(Exception):
            decode_object("!!!notbase64!!!")

    def test_decode_object_invalid_json(self):
        # valid base64, but not valid JSON
        bad = base64.b64encode(b"not json").decode()
        with pytest.raises(Exception):
            decode_object(bad)


class TestGetTokenHash:
    # --- get_token_hash ---
    def test_get_token_hash(self):
        with patch("agent_leasing.util.helpers.settings.identity_secret_token", new="mysecret"):
            expected = __import__("hashlib").sha256(b"mysecret").hexdigest()
            assert get_token_hash() == expected


class TestHumanizeNumbers:
    def test_humanize_numbers_full_sentence(self):
        sentence = "I paid $1,315.91 on 2025-10-13 and have 2.5 cats."
        assert humanize_numbers(sentence) == (
            "I paid one thousand, three hundred and fifteen dollars, ninety-one cents "
            "on two thousand and twenty-five-ten-thirteen and have two point five cats."
        )

    def test_humanize_numbers_no_change(self):
        sentence = "No numbers here!"
        assert humanize_numbers(sentence) == "No numbers here!"

    def test_rent_details_response(self):
        sentence = (
            '{"reason":"User asked for their rent amount and rent details; '
            'fetched rent information via required tool call.","suggested_response":'
            '"Your monthly rent is $1,899.92. Rent is due on 2025-09-01. Your current '
            "balance is $123.45, which includes the following charges: Rent $1,899.00, "
            "Application Fee $25.00, Non refundable Fee $250.00, Admin Fee $100.00, "
            'Trash Charge $18.55. How else can I help?","detailed_information":"Tool response '
            "(status_code: ok): balance: 123.45 USD; rent: 1899.00 USD; rent_due_date: 2025-09-01; "
            "charges: [{transactionDesc: 'Application Fee', date: '05/09/2025', amount: 25.0}, "
            "{transactionDesc: 'Non refundable Fee', date: '05/09/2025', amount: 250.0}, "
            "{transactionDesc: 'Admin Fee', date: '05/09/2025', amount: 100.0}, {transactionDesc: "
            "'Trash Charge', date: '05/09/2025', amount: 18.55}, {transactionDesc: 'Rent', date: "
            "'05/09/2025', amount: 1899.0}]\"}"
        )

        assert humanize_numbers(sentence) == (
            '{"reason":"User asked for their rent amount and rent details; '
            'fetched rent information via required tool call.","suggested_response":'
            '"Your monthly rent is one thousand, eight hundred and ninety-nine dollars, '
            "ninety-two cents. Rent is due on two thousand and twenty-five-nine-one. "
            "Your current balance is one hundred and twenty-three dollars, forty-five cents, "
            "which includes the following charges: Rent one thousand, eight hundred "
            "and ninety-nine dollars, zero cents, Application Fee twenty-five dollars, "
            "zero cents, Non refundable Fee two hundred and fifty dollars, zero cents, "
            "Admin Fee one hundred dollars, zero cents, Trash Charge eighteen dollars, "
            'fifty-five cents. How else can I help?","detailed_information":"Tool response '
            "(status_code: ok): balance: one hundred and twenty-three point four five USD; "
            "rent: one thousand, eight hundred and ninety-nine USD; rent_due_date: "
            "two thousand and twenty-five-nine-one; charges: "
            "[{transactionDesc: 'Application Fee', date: 'five/nine/two thousand and twenty-five', "
            "amount: twenty-five}, {transactionDesc: 'Non refundable Fee', "
            "date: 'five/nine/two thousand and twenty-five', amount: two hundred and fifty}, "
            "{transactionDesc: 'Admin Fee', date: 'five/nine/two thousand and twenty-five', amount: one hundred}, "
            "{transactionDesc: 'Trash Charge', date: 'five/nine/two thousand and twenty-five', "
            "amount: eighteen point five five}, {transactionDesc: 'Rent', "
            "date: 'five/nine/two thousand and twenty-five', amount: one thousand, eight hundred and ninety-nine}]\"}"
        )


class TestResolveGreetingPlaceholders:
    def test_none_greeting_returns_none(self):
        assert resolve_greeting_placeholders(None) is None

    def test_empty_greeting_returns_empty(self):
        assert resolve_greeting_placeholders("") == ""

    def test_no_placeholders_unchanged(self):
        assert (
            resolve_greeting_placeholders(
                "Welcome to Oakwood!",
                first_name="John",
                property_name="Oakwood",
            )
            == "Welcome to Oakwood!"
        )

    def test_first_name_substitution(self):
        assert resolve_greeting_placeholders("Hello [first_name]!", first_name="Jane") == "Hello Jane!"

    def test_last_name_substitution(self):
        assert resolve_greeting_placeholders("Hi Mr. [last_name]", last_name="Smith") == "Hi Mr. Smith"

    def test_property_name_substitution(self):
        assert (
            resolve_greeting_placeholders("Welcome to [property_name]", property_name="Oakwood")
            == "Welcome to Oakwood"
        )

    def test_all_placeholders_together(self):
        assert (
            resolve_greeting_placeholders(
                "Hi [first_name] [last_name], welcome to [property_name]!",
                first_name="Jane",
                last_name="Smith",
                property_name="Oakwood",
            )
            == "Hi Jane Smith, welcome to Oakwood!"
        )

    def test_missing_field_cleans_orphan_comma(self):
        # "Hello , welcome" -> "Hello, welcome" (space before comma removed)
        assert resolve_greeting_placeholders("Hello [first_name], welcome", first_name=None) == "Hello, welcome"

    def test_missing_field_cleans_double_space(self):
        # "Hi Jane  today" -> "Hi Jane today"
        assert (
            resolve_greeting_placeholders("Hi [first_name] [last_name] today", first_name="Jane", last_name=None)
            == "Hi Jane today"
        )

    def test_trailing_placeholder_missing_strips_whitespace(self):
        # "Welcome to " -> "Welcome to"
        assert resolve_greeting_placeholders("Welcome to [property_name]", property_name=None) == "Welcome to"

    def test_repeated_placeholder_all_substituted(self):
        assert resolve_greeting_placeholders("[first_name] [first_name]!", first_name="Jane") == "Jane Jane!"

    def test_kb_example_from_ticket(self):
        """The exact KB greeting format that triggered the bug on alpha."""
        assert (
            resolve_greeting_placeholders(
                "Hello [first_name], This is a custom greeting for [property_name] from beta environment.",
                first_name="Jane",
                property_name="Oakwood",
            )
            == "Hello Jane, This is a custom greeting for Oakwood from beta environment."
        )
