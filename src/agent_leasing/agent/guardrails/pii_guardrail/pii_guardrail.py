"""
PII Output Guardrail

This module implements an output guardrail that detects and prevents
personally identifiable information (PII) from being exposed in agent responses.
"""

import asyncio
import re
from typing import Any, Final

from agents import (
    Agent,
    GuardrailFunctionOutput,
    RunContextWrapper,
    TResponseInputItem,
    input_guardrail,
    output_guardrail,
)
from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine
from pydantic import BaseModel

from agent_leasing.agent.guardrails.text_utils import (
    extract_text_from_input,
    extract_text_from_output,
)
from agent_leasing.models.context import SessionScope
from agent_leasing.util.language_utils import localize_guardrail_response

_SAFE_RESPONSE: Final[str] = (
    "I'm sorry, but I cannot process requests containing personal information. How else can I assist you today?"
)


class PIIDetectionResult(BaseModel):
    """Result of PII detection.  Only used in a helper function"""

    contains_pii: bool
    pii_types_found: list[str]
    reasoning: str
    redacted_text: str


class PIIGuardrailOutput(BaseModel):
    """Standard payload returned when the PII guardrail blocks output."""

    reasoning: str
    pii_types_found: list[str]
    is_pii: bool
    safe_response: str = _SAFE_RESPONSE

    @property
    def labels(self) -> list[str]:
        return self.pii_types_found


# TODO: move this to a sidecar container
# TODO: make sure this checks for PII that is verbally read ("one two three" vs "123")

# Initialize Presidio analyzer engine
# Using a lightweight NLP engine for better performance
nlp_configuration = {
    "nlp_engine_name": "spacy",
    "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
}
provider = NlpEngineProvider(nlp_configuration=nlp_configuration)
nlp_engine = provider.create_engine()

# Create a custom credit card recognizer to handle formatted numbers
formatted_credit_card_recognizer = PatternRecognizer(
    supported_entity="CREDIT_CARD",
    patterns=[
        # Pattern for credit cards with spaces (e.g., "4929 1345 8263 4852")
        Pattern(name="credit_card_spaces", regex=r"\b(?:\d{4}\s){3}\d{4}\b", score=0.8),
        # Pattern for credit cards with hyphens (e.g., "6011-8123-4567-4329")
        Pattern(name="credit_card_hyphens", regex=r"\b(?:\d{4}-){3}\d{4}\b", score=0.8),
        # Pattern for Amex format with spaces (e.g., "3714 496353 98431")
        Pattern(name="amex_spaces", regex=r"\b\d{4}\s\d{6}\s\d{5}\b", score=0.8),
        # Pattern for Amex format with hyphens (e.g., "3714-496353-98431")
        Pattern(name="amex_hyphens", regex=r"\b\d{4}-\d{6}-\d{5}\b", score=0.8),
    ],
    context=["card", "credit", "payment", "visa", "mastercard", "amex", "discover"],
)

# Create a more restrictive phone number recognizer that only matches properly formatted phone numbers
# This avoids false positives with tracking numbers and ID numbers
custom_phone_recognizer = PatternRecognizer(
    supported_entity="PHONE_NUMBER",
    patterns=[
        # US phone patterns with explicit formatting (no plain 10-digit sequences)
        Pattern(name="us_phone_formatted", regex=r"\b\d{3}-\d{3}-\d{4}\b", score=0.9),
        Pattern(name="us_phone_parentheses", regex=r"\b\(\d{3}\)\s?\d{3}-\d{4}\b", score=0.9),
        Pattern(name="us_phone_dots", regex=r"\b\d{3}\.\d{3}\.\d{4}\b", score=0.9),
        Pattern(name="us_phone_spaces", regex=r"\b\d{3}\s\d{3}\s\d{4}\b", score=0.9),
        # International formats with country codes
        Pattern(
            name="intl_phone_plus",
            regex=r"\+\d{1,3}[-.\s]?\d{3}[-.\s]?\d{3}[-.\s]?\d{4}",
            score=0.9,
        ),
        Pattern(
            name="intl_phone_plus_alt",
            regex=r"\+\d{1,3}[-.\s]?\d{3}[-.\s]?\d{3}[-.\s]?\d{3}[-.\s]?\d{1,4}",
            score=0.8,
        ),
        # 10-digit numbers ONLY when preceded by explicit phone context words
        # Exclude tracking/reference/order numbers by being more specific about phone contexts
        Pattern(
            name="us_phone_10digit_with_context",
            regex=r"(?i)(?:call|phone|contact|reach|dial|tel|mobile|cell)(?:\s+me)?(?:\s+at)?\s+\d{10}\b|(?:phone|mobile|cell)\s+number(?:\s+is)?\s+\d{10}\b",
            score=0.8,
        ),
    ],
    context=[
        "call",
        "phone",
        "number",
        "contact",
        "reach",
        "dial",
        "tel",
        "mobile",
        "cell",
    ],
)

# Initialize analyzer with custom recognizers
analyzer = AnalyzerEngine(nlp_engine=nlp_engine)
analyzer.registry.add_recognizer(formatted_credit_card_recognizer)
# Remove the default phone recognizer and add our custom one
analyzer.registry.remove_recognizer("PhoneRecognizer")
analyzer.registry.add_recognizer(custom_phone_recognizer)

anonymizer = AnonymizerEngine()


# Entity type mapping for user-friendly names
ENTITY_TYPE_MAPPING = {
    "EMAIL_ADDRESS": "email address",
    "PHONE_NUMBER": "phone number",
    "US_SSN": "social security number",
    "CREDIT_CARD": "credit card number",
    "US_DRIVER_LICENSE": "driver license",
    "US_PASSPORT": "passport number",
    "IP_ADDRESS": "IP address",
    "IBAN_CODE": "IBAN code",
    "US_BANK_NUMBER": "bank account number",
}

# Business context keywords that indicate a phone number is for property services, not personal PII
# These are legitimate business contacts that should be allowed in agent responses
BUSINESS_PHONE_CONTEXTS = [
    "towing",
    "tow",
    "property",
    "office",
    "management",
    "maintenance",
    "emergency",
    "service",
    "contact",
    "leasing",
    "front desk",
    "reception",
]


def is_business_phone_number(text: str, phone_start: int, phone_end: int) -> bool:
    """
    Determine if a phone number is a business contact (property service) or personal PII.

    Args:
        text: The full text being analyzed
        phone_start: Start position of the phone number
        phone_end: End position of the phone number

    Returns:
        True if the phone number appears in a business context, False if it's likely personal PII
    """
    # Get context around the phone number (50 characters before and after)
    context_start = max(0, phone_start - 50)
    context_end = min(len(text), phone_end + 50)
    context = text[context_start:context_end].lower()

    # Check if any business context keywords appear near the phone number
    for keyword in BUSINESS_PHONE_CONTEXTS:
        if keyword in context:
            return True

    return False


# Context patterns (compiled regexes with word boundaries) that indicate a
# US_DRIVER_LICENSE match is actually a vehicle license plate, not a personal
# driver's license number — avoids false positives in parking pass flows.
_LICENSE_PLATE_PATTERNS = re.compile(
    r"\b(?:"
    r"license\s*plate"
    r"|plate\s*(?:number|#)"
    r"|parking\s*pass"
    r"|parking"
    r"|guest\s*pass"
    r"|vehicle"
    r"|tag\s*number"
    r"|registration"
    r")\b",
    re.IGNORECASE,
)


def is_license_plate_context(text: str, match_start: int, match_end: int) -> bool:
    """Determine if a US_DRIVER_LICENSE match is actually a vehicle license plate.

    Checks for license-plate-related keywords (with word boundaries) near the
    match to distinguish vehicle plates (e.g., "TRX-W011" in a guest parking
    flow) from actual driver's license numbers.

    Args:
        text: The full text being analyzed
        match_start: Start position of the matched token
        match_end: End position of the matched token

    Returns:
        True if the match appears in a license plate context, False otherwise
    """
    context_start = max(0, match_start - 50)
    context_end = min(len(text), match_end + 50)
    context = text[context_start:context_end]

    return bool(_LICENSE_PLATE_PATTERNS.search(context))


ENTITIES_TO_DETECT = [
    # "EMAIL_ADDRESS",
    # "PHONE_NUMBER",
    "US_SSN",
    "CREDIT_CARD",
    "US_DRIVER_LICENSE",
    "US_PASSPORT",
    "IP_ADDRESS",
    "IBAN_CODE",
    "US_BANK_NUMBER",
]


def detect_pii(text: str, redact_pii: bool = False) -> PIIDetectionResult:
    """
    Detect PII in the given text using Microsoft Presidio.

    This uses Presidio's advanced NLP-based approach for more accurate
    PII detection compared to simple regex patterns.
    """
    try:
        # Analyze text for PII entities with higher confidence threshold to reduce false positives
        results = analyzer.analyze(
            text=text,
            language="en",
            entities=ENTITIES_TO_DETECT,
            score_threshold=0.5,
        )

        # Extract unique PII types found, filtering out business phone numbers
        pii_types_found = []
        detected_entities = set()

        for result in results:
            entity_type = result.entity_type

            # Special handling for phone numbers: check if it's a business contact
            if entity_type == "PHONE_NUMBER":
                if is_business_phone_number(text, result.start, result.end):
                    # This is a business phone number (e.g., towing service), not personal PII
                    continue

            # Special handling for driver's license: check if it's a vehicle license plate
            # TODO: Refactor special handling for False Positive and False Negative corrections
            if entity_type == "US_DRIVER_LICENSE":
                if is_license_plate_context(text, result.start, result.end):
                    continue

            if entity_type not in detected_entities:
                detected_entities.add(entity_type)
                # Map to user-friendly names
                friendly_name = ENTITY_TYPE_MAPPING.get(entity_type, entity_type.lower().replace("_", " "))
                pii_types_found.append(friendly_name)

        contains_pii = len(pii_types_found) > 0

        # initialize redacted text to original text, replacing if we need to redact
        redacted_text = text
        if contains_pii:
            reasoning = f"Found PII types: {', '.join(pii_types_found)}"

            if redact_pii:
                anonymized_result = anonymizer.anonymize(
                    text=text,
                    analyzer_results=results,
                )
                redacted_text = anonymized_result.text
        else:
            reasoning = "No PII detected in the response"

        return PIIDetectionResult(
            contains_pii=contains_pii,
            pii_types_found=pii_types_found,
            reasoning=reasoning,
            redacted_text=redacted_text,
        )

    except Exception as e:
        # Fallback in case of any Presidio errors
        return PIIDetectionResult(
            contains_pii=True,  # Err on the side of caution
            pii_types_found=["detection error"],
            reasoning=f"PII detection failed: {str(e)}",
            redacted_text=text,  # Return original text if redaction fails
        )


async def _check_pii(
    original_content: Any,
    content_type: str,  # "input" or "output"
    language_code: str,
) -> GuardrailFunctionOutput:
    """
    Common helper that checks for PII in content.

    Args:
        text: The extracted text content to check for PII
        original_content: The original input/output to pass through if no PII detected
        content_type: Either "input" or "output" for messaging purposes
    """
    # Detect PII in the text
    if content_type == "input":
        text = extract_text_from_input(original_content)
    elif content_type == "output":
        text = extract_text_from_output(original_content)
    else:
        raise ValueError(f"Invalid content type: {content_type}")

    pii_result = await asyncio.to_thread(detect_pii, text)

    if pii_result.contains_pii:
        # PII detected - return safe response with appropriate message

        safe_response = await localize_guardrail_response(
            base_response=_SAFE_RESPONSE,
            guardrail_name="pii_guardrail",
            original_content=original_content,
            content_type=content_type,
            language_code=language_code,
        )

        return GuardrailFunctionOutput(
            output_info=PIIGuardrailOutput(
                reasoning=pii_result.reasoning,
                pii_types_found=pii_result.pii_types_found,
                is_pii=True,
                safe_response=safe_response,
            ),
            tripwire_triggered=True,
        )

    # No PII detected - pass through the original content
    return GuardrailFunctionOutput(
        output_info=original_content,
        tripwire_triggered=False,
    )


@input_guardrail
async def pii_input_guardrail(
    ctx: RunContextWrapper[SessionScope],
    agent: Agent,
    input: str | list[TResponseInputItem],
) -> GuardrailFunctionOutput:
    """Input guardrail that checks for PII in user input."""
    return await _check_pii(input, "input", ctx.context.language_code)


@output_guardrail
async def pii_output_guardrail(
    ctx: RunContextWrapper[SessionScope],
    agent: Agent,
    output: Any,
) -> GuardrailFunctionOutput:
    """Output guardrail that checks for PII in agent responses."""
    return await _check_pii(output, "output", ctx.context.language_code)
