import json
import os
import uuid
from enum import Enum
from typing import Any, Self

import structlog
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_leasing.settings import settings

logger = structlog.get_logger(__name__)


class Examples:
    """Load examples."""

    def __init__(
        self,
    ):
        """Load JSON that functions as a context and is sent to v1/agents/ask endpoint."""
        # Use sandbox example data when SANDBOX_NAME is set
        sandbox_name = os.environ.get("SANDBOX_NAME")
        chat_example_file = (
            "example_data/resident/chat/example_ask_request_ll_sandbox.json"
            if sandbox_name
            else "example_data/resident/chat/example_ask_request_ll.json"
        )
        with open(
            os.path.join(os.path.dirname(__file__), chat_example_file),
            encoding="utf-8",
        ) as file:
            self.ASK_REQUEST_RESIDENT_CHAT_LL = json.load(file)

        with open(
            os.path.join(
                os.path.dirname(__file__),
                "example_data/resident/sms/example_ask_request_knck.json",
            ),
            encoding="utf-8",
        ) as file:
            self.ASK_REQUEST_RESIDENT_SMS_KNCK = json.load(file)

        with open(
            os.path.join(
                os.path.dirname(__file__),
                "example_data/resident/sms/example_ask_request_ll.json",
            ),
            encoding="utf-8",
        ) as file:
            self.ASK_REQUEST_RESIDENT_SMS_LL = json.load(file)

        with open(
            os.path.join(
                os.path.dirname(__file__),
                "example_data/resident/email/example_ask_request_knck.json",
            ),
            encoding="utf-8",
        ) as file:
            self.ASK_REQUEST_RESIDENT_EMAIL_KNCK = json.load(file)

        with open(
            os.path.join(
                os.path.dirname(__file__),
                "example_data/resident/email/example_ask_request_ll.json",
            ),
            encoding="utf-8",
        ) as file:
            self.ASK_REQUEST_RESIDENT_EMAIL_LL = json.load(file)

        with open(
            os.path.join(
                os.path.dirname(__file__),
                "example_data/resident/voice/example_ask_request_knck.json",
            ),
            encoding="utf-8",
        ) as file:
            self.ASK_REQUEST_RESIDENT_VOICE_KNCK = json.load(file)


examples = Examples()


class AIConfig(BaseModel):
    """This is the ai_config section of what gets passed into the /v1/agents/ask endpoint."""

    is_sms_enabled: bool = False
    is_gen_ai_sms_enabled: bool = False
    resident_virtual_agent_sms: bool = False
    resident_virtual_agent_sms_gen_ai: bool = False
    is_chat_enabled: bool = False
    is_gen_ai_chat_enabled: bool = False
    resident_virtual_agent_chat: bool = False
    resident_virtual_agent_chat_gen_ai: bool = False
    chat_rollover: str | None = None
    schedule_tour_va_enabled: bool = False
    pna_va_enabled: bool = False
    rpcc_agent_rollover: bool = False
    call_routing: str | None = "ALL"
    is_gen_ai_voice_enabled: bool = False
    resident_virtual_agent_voice: bool = False
    resident_virtual_agent_voice_gen_ai: bool = False

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)


class UCReference(BaseModel):
    id: int | str  # Allow both integers and UUID strings
    source: str | None


class PropertyAddress(BaseModel):
    city: str
    house: str | None = None
    neighborhood: str | None = None
    raw: str
    state: str
    street: str
    zip: str


class OfficeHour(BaseModel):
    start_time: str | None = None
    end_time: str | None = None
    is_active: bool = False


class PropertyPreferences(BaseModel):
    ai_cross_sell_availability_url: str | None = None
    in_person_tours: bool = False
    live_video_tour_type: bool | str | None = False
    self_guided_tour_button_label: str | None = None
    self_guided_tour_url: str | None = None
    self_guided_tours_enabled: bool = False
    tours_export_only_favorite_unit: bool = False
    virtual_tour_links: bool = False
    virtual_tour_links_mapping: list = []

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)


class EmailChat(BaseModel):
    email_subject: str
    knock_property_id: str
    knock_resident_id: str
    knock_resident_email: str
    knock_resident_source: str | None = None
    resident_assigned_manager_id: str
    original_text: str
    html: str
    email_source: str
    thread_id: str
    thread_message_chat_id: str


class StaticPaths(BaseModel):
    payment_and_ledger: str | None = None
    amenities: str | None = None
    reservations: str | None = None
    parking: str | None = None
    package: str | None = None
    community_events: str | None = None
    human_hand_off: str | None = None
    service_request: str | None = None
    front_desk_instructions: str | None = None
    resident_checklist: str | None = None
    parking_passes: str | None = None
    community_wall: str | None = None
    single_service_request: str | None = None
    all_open_service_request: str | None = None
    leasing: str | None = None


class ProductInfo(BaseModel):
    """This is the product_info section of what gets passed into the /v1/agents/ask endpoint."""

    knock_property_id: str
    knock_prospect_id: str | None = None
    knock_applicant_id: str | None = None
    knock_application_id: str | None = None
    knock_resident_id: str | None = None  # Added from real payloads
    prospect_renter_id: str | None = None
    call_sid: str | None = None
    should_record: bool = False
    caller: str | None = None
    callee: str | None = None
    call_routing: str = "all"
    thread_id: str | None = None
    resident_manager_id: str | None = None
    resident_assigned_manager_id: int | None = None
    resident_renter_id: str | None = None
    source: str | None = None
    property_name: str | None = None
    property_address: PropertyAddress | None = None
    pmc_id: str | None = None
    pmc_name: str | None = None
    office_hours: dict[str, OfficeHour] | None = None
    property_preferences: PropertyPreferences | None = None
    property_timezone: str | None = None
    uc_first_name: str | None = None
    uc_last_name: str | None = None
    uc_company_id: UCReference | None = None
    uc_property_id: UCReference | None = None
    uc_guestcard_id: UCReference | None = None
    uc_customer_id: UCReference | None = None
    uc_resident_household_id: UCReference | None = None
    uc_resident_member_id: UCReference | None = None
    # emergency service request fields.
    # `dispatch_schedule_active` accepts legacy bool (True=ADVANCED, False=BASIC) AND new
    # SKU strings ("BASIC", "ADVANCED", "RPCC"; legacy upstream values "AI Maintenance", "AA",
    # "None" still map). See _resolve_emergency_product_from_code.
    lo_property_id: str | None = None
    dispatch_schedule_active: str | bool | None = None
    emerg_phone: str | None = None
    resident_phone: str | None = None
    # Additional resident fields from real payloads
    uc_community_id: UCReference | None = None
    uc_community_uuid: UCReference | None = None
    uc_consumer_identity_token: UCReference | None = None
    ab_resident_id: UCReference | None = None
    ab_resident_uuid: UCReference | None = None
    ab_unit_id: UCReference | None = None
    # Static paths for navigation
    uc_portal_base_url: str | None = None
    uc_lease_id: UCReference | None = None
    uc_person_id: UCReference | None = None
    ai_config: AIConfig = AIConfig()
    email_chat: EmailChat | None = None
    static_paths: StaticPaths | None = None
    # Infosec authentication fields
    date_of_birth: str | None = None
    ab_building_number: str | None = None
    ab_unit_number: str | None = None
    # Custom greeting from Knowledge Base (populated by GenAI service)
    custom_greeting: str | None = None
    # Former-resident sub-flow gate. When set to "balance_resolution", the agent
    # restricts itself to the Policy and Ledger workflow only — every other
    # workflow is treated as off-topic. ``None`` preserves the normal scope.
    former_type: str | None = None

    @property
    def prospect_id(self):
        """Convenience method to get prospect_id."""
        return self.knock_prospect_id

    @property
    def knock_company_id(self) -> str | None:
        """`pmc_id` is the Knock company ID — see cai-genai-service `mlops.client.ts:180`."""
        return self.pmc_id

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)


class Product(str, Enum):
    """A product is synonymous with an agent name."""

    SIMPLE = "simple"
    RESIDENT_ONE_CHAT = "resident_one_chat"
    RESIDENT_ONE_SMS = "resident_one_sms"
    RESIDENT_ONE_EMAIL = "resident_one_email"
    RESIDENT_ONE_VOICE = "resident_one_voice"


class Persona(str, Enum):
    """A persona is the type of customer that is interacting with the agent."""

    APPLICANT = "applicant"
    PROSPECT = "prospect"
    RESIDENT = "resident"


class Channel(str, Enum):
    """A channel is the communication method the customer is using to interact with the agent."""

    VOICE = "voice"
    SMS = "sms"
    CHAT = "chat"
    EMAIL = "email"


class ApiUser(BaseModel):
    """Schema for Authenticating to RealPage APIs. Use request.product_info for other data."""

    vanity_host: str
    company_id: str
    property_id: str
    user_id: str
    user_auth_token: str | None = None


class RequestType(str, Enum):
    STANDARD = "standard"
    VOICE = "voice"


class EmergencyServiceProduct(str, Enum):
    BASIC = "BASIC"
    ADVANCED = "ADVANCED"
    RPCC = "RPCC"


class HandoffReasonCode(str, Enum):
    """Why the agent handed off to staff. Set by the agent on every
    transfer_to_staff_* call (mutually exclusive — exactly one per call).
    Drives the `Handoff to Staff - <Sub-label>` activity sub-label and
    the `extra.handoff_reason` field on the TaskActivityEvent.

    `ALREADY_IN_HANDOFF` is the only value that does NOT come from a tool
    call — it is emitted from the SMS/EMAIL short-circuit at
    `server.py::_handle_active_handoff` where the agent is skipped
    entirely.
    """

    RESIDENT_REQUESTED = "RESIDENT_REQUESTED"
    SYSTEM_ERROR = "SYSTEM_ERROR"
    EMERGENCY = "EMERGENCY"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    MISSING_DATA = "MISSING_DATA"
    ALREADY_IN_HANDOFF = "ALREADY_IN_HANDOFF"
    COMPLAINT = "COMPLAINT"


class HandoffTopic(str, Enum):
    """What the handoff conversation is about. Optional, orthogonal to
    `HandoffReasonCode` (which says WHY we handed off). Set by the agent
    on transfer_to_staff_* calls when the conversation matches one of
    the listed topics; left unset otherwise. Lands in
    `extra.handoff_topic` on the TaskActivityEvent when set; omitted
    otherwise.
    """

    BALANCE_RESOLUTION = "BALANCE_RESOLUTION"


# Upstream `dispatch_schedule_active` string -> internal enum.
# Accepts both new SKU names and legacy upstream values for backwards compat.
_PRODUCT_CODE_MAP: dict[str, EmergencyServiceProduct] = {
    # New SKU names
    "BASIC": EmergencyServiceProduct.BASIC,
    "ADVANCED": EmergencyServiceProduct.ADVANCED,
    "RPCC": EmergencyServiceProduct.RPCC,
    # Legacy upstream SKU codes
    "None": EmergencyServiceProduct.BASIC,
    "AI Maintenance": EmergencyServiceProduct.ADVANCED,
    "AA": EmergencyServiceProduct.ADVANCED,
}


class AskRequest(BaseModel):
    """This is what gets passed into the /v1/agents/ask endpoint."""

    task_instructions: str | None = None
    request_id: str | None = None
    request_type: RequestType = RequestType.STANDARD
    product: str | Product
    product_info: ProductInfo
    prompt: str = ""
    prompt_version: int | None = 0
    confirmation: str = ""
    chat_session_id: str = str(uuid.uuid4().hex)
    flow_id: str | None = None
    user: ApiUser | None = None  # Optional field to authenticate to RealPage APIs.  In Agentix, not in confluence
    logs: str = ""
    voice_prompt: list[dict] = Field(default_factory=list)
    language_code: str = ""
    state: str | None = None  # Accepted values: None, done, resumable, pending_confirmation, error
    property_name: str | None = None
    is_load_test: bool = False

    @property
    def property_id(self):
        """Convenience method to get property_id."""
        return self.product_info.knock_property_id

    @property
    def prospect_id(self):
        """Convenience method to get prospect_id."""
        return self.product_info.knock_prospect_id

    @property
    def resident_id(self):
        return self.product_info.knock_resident_id or getattr(self.product_info.uc_resident_member_id, "id", None)

    @property
    def conversation_type(self) -> Channel:
        if "chat" in self.product:
            return Channel.CHAT
        elif "sms" in self.product:
            return Channel.SMS
        elif "email" in self.product:
            return Channel.EMAIL
        elif "voice" in self.product:
            return Channel.VOICE
        return Channel.CHAT

    @property
    def callback_number(self) -> str | None:
        """Preferred callback number; favor caller ID, then resident phone, then emerg_phone."""
        if "voice" in self.product:
            return self.product_info.caller or self.product_info.resident_phone
        else:  # SMS, EMAIL, CHAT in product_str
            return self.product_info.resident_phone or self.product_info.caller

    # Required fields per persona (dot-notation paths from AskRequest root)
    # Fields natively required (not default value set) need not be present in _REQUIRED_FIELDS
    _REQUIRED_FIELDS: dict[Persona, list[str]] = {
        Persona.RESIDENT: [
            "product_info.uc_company_id",
            "product_info.uc_property_id",
            "product_info.uc_resident_household_id",
            "product_info.uc_resident_member_id",
            "product_info.ab_resident_id",
            "product_info.uc_lease_id",
            "product_info.uc_portal_base_url",
        ],
    }

    @property
    def persona(self) -> Persona:
        """Determine the persona based on the product type."""
        product = self.product if isinstance(self.product, str) else self.product.value
        if "resident" in product:
            return Persona.RESIDENT
        return Persona.PROSPECT

    def _get_field_value(self, path: str):
        """Get a field value using dot-notation path (e.g., 'product_info.uc_company_id')."""
        obj = self
        for attr in path.split("."):
            obj = getattr(obj, attr, None)
            if obj is None:
                return None
        return obj

    def is_valid(self) -> bool:
        """
        Validate required fields based on persona.

        We may want to also do this by channel in the future. In that case it might be better to use
        separate objects and validate using the simpler built-in Pydantic validation."""
        required_fields = self._REQUIRED_FIELDS.get(self.persona, [])
        return all(self._get_field_value(field) for field in required_fields)

    def _get_missing_fields(self) -> list[str]:
        """Return list of missing required fields for the current persona."""
        required_fields = self._REQUIRED_FIELDS.get(self.persona, [])
        return [field for field in required_fields if not self._get_field_value(field)]

    @property
    def resident_data(self) -> dict[str, int]:
        """Get resident data in a clean, simple format."""
        product_info = self.product_info

        return {
            "first_name": product_info.uc_first_name,
            "pmc_id": product_info.uc_company_id.id if product_info.uc_company_id else None,
            "site_id": product_info.uc_property_id.id if product_info.uc_property_id else None,
            "resident_household_id": product_info.uc_resident_household_id.id
            if product_info.uc_resident_household_id
            else None,
            "resident_member_id": product_info.uc_resident_member_id.id
            if product_info.uc_resident_member_id
            else None,
            "community_id": product_info.uc_community_id.id if product_info.uc_community_id else None,
            "resident_id": product_info.ab_resident_id.id if product_info.ab_resident_id else None,
            # data for sending SMS via voice
            "knock_resident_id": product_info.knock_resident_id  # it's just a string
            if product_info.knock_resident_id
            else None,
            "thread_id": product_info.thread_id if product_info.thread_id else None,
            "send_as_manager_id": product_info.resident_manager_id if product_info.resident_manager_id else None,
        }

    @property
    def emergency_service_product(self) -> EmergencyServiceProduct:
        """Determine emergency service product from `dispatch_schedule_active`."""
        is_voice = "voice" in self.product
        return _resolve_emergency_product_from_code(
            self.product_info.dispatch_schedule_active,
            self.product_info.lo_property_id,
            is_voice=is_voice,
        )

    @model_validator(mode="after")
    def validate_persona_fields(self) -> Self:
        """Validate required fields based on persona."""
        if not self.is_valid():
            missing = self._get_missing_fields()
            raise ValueError(f"Missing required fields for {self.persona.value} persona: {', '.join(missing)}")
        return self

    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)


def _resolve_emergency_product_from_code(
    dispatch_schedule_active: str | bool | None,
    lo_property_id: str | None,
    *,
    is_voice: bool = False,
) -> EmergencyServiceProduct:
    """Map upstream `dispatch_schedule_active` to EmergencyServiceProduct.

    Accepts legacy bool (True=ADVANCED, False=BASIC) and SKU strings (see _PRODUCT_CODE_MAP).
    """
    if dispatch_schedule_active is None or dispatch_schedule_active is False:
        return EmergencyServiceProduct.BASIC

    if dispatch_schedule_active is True:
        # Legacy bool: True meant "advanced dispatch is active"
        product = EmergencyServiceProduct.ADVANCED
    else:
        product = _PRODUCT_CODE_MAP.get(dispatch_schedule_active)
        if product is None:
            logger.warning("Unknown dispatch_schedule_active=%r, defaulting to BASIC", dispatch_schedule_active)
            return EmergencyServiceProduct.BASIC

    if product == EmergencyServiceProduct.BASIC:
        return EmergencyServiceProduct.BASIC

    # ADVANCED and RPCC require lo_property_id
    if not lo_property_id:
        logger.warning(
            "dispatch_schedule_active=%r requires lo_property_id but it's missing; falling back to BASIC",
            dispatch_schedule_active,
        )
        return EmergencyServiceProduct.BASIC

    # Check feature flags
    if product == EmergencyServiceProduct.ADVANCED and not settings.emergency_service_transfer_advanced_enabled:
        return EmergencyServiceProduct.BASIC
    if product == EmergencyServiceProduct.RPCC and not settings.emergency_service_transfer_rpcc_enabled:
        return EmergencyServiceProduct.BASIC

    # For non-voice channels, RPCC is not production-ready yet — route to ADVANCED instead.
    # The RPCC non-voice tool code path still exists; to re-enable it, change this mapping
    # once the outbound-call flow is validated in prod.
    if product == EmergencyServiceProduct.RPCC and not is_voice:
        # Respect the ADVANCED kill switch — don't smuggle RPCC traffic through it.
        if not settings.emergency_service_transfer_advanced_enabled:
            return EmergencyServiceProduct.BASIC
        return EmergencyServiceProduct.ADVANCED

    return product


class AskChatPayload(BaseModel):
    response: str
    languageCode: str = "en"


class AskContent(BaseModel):
    # Spec requires a *stringified JSON* for `chat`
    chat: str


class AskResponse(BaseModel):
    request_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    content: AskContent | None = None
    flow_id: str | None = None
    flow_name: str | None = None
    state: str = "done"
    chat_session_id: str | None = None
    langsmith_trace_url: str | None = None
    streamable: bool = False


class Flow(BaseModel):
    name: str
    display_name: str

    def __init__(self, name: str) -> None:
        if name == "END":
            display_name = "END"
        else:
            name = name.replace("thinker_tool", "flow").upper()
            display_name = name.replace("_", " ").title()
        super().__init__(name=name, display_name=display_name)

    def __hash__(self) -> int:
        return hash(self.name)


class BotType(str, Enum):
    RESIDENT = "RESIDENT"
    PROSPECT = "PROSPECT"
    APPLICANT = "APPLICANT"


class Author(str, Enum):
    CONTACT = "CONTACT"
    BOT = "BOT"
    UNKNOWN = "UNKNOWN"
