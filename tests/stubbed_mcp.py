"""
MCP server with canned tool responses for testing.
"""

from datetime import datetime
from typing import Annotated, Any, Dict, Literal, Optional

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from tests.date_helpers import (
    format_event_date,
    generate_date_iso,
    generate_date_mmddyyyy,
    generate_datetime_string,
)

mcp = FastMCP("Stubbed MCP")


class ResidentUpdateRequest(BaseModel):
    resident_id: int | str
    sms_consent: bool


NON_CONSENTING_RESIDENT_ID = 9999
NEW_RESIDENT_ID = 9998


@mcp.tool(
    name="get_property_overview",
    title="Get Property Overview",
    description="""
        Retrieve a comprehensive overview of a real estate property with detailed information organized in a structured summary format.

        RETURN FORMAT: Returns a dictionary with:
        - property_id: The property ID that was queried
        - summary: A formatted markdown string containing all property information

        INFORMATION AVAILABLE IN SUMMARY:

        **Basic Property Info:**
        - Apartment Complex name
        - Full address (street, city, state, zip)
        - Neighborhood name
        - Property description
        - Year built
        - Number of units

        **Contact Information:**
        - Phone number
        - Email availability status
        - Website URL
        
        **Towing Service:** Information related to towing including towing service company and its contact
        
        **Office Hours:**
        - Complete weekly schedule (Monday-Sunday)
        - Specific hours for each day
        - Days when office is closed

        **Apartment Features:**
        - All available appliances (dishwasher, refrigerator, etc.)
        - Flooring types (hardwood, etc.)
        - Special features (walk-in closets, balconies, etc.)
        - Kitchen features (quartz countertops, modern appliances)
        - Laundry information (in-unit, shared)
        - Furnishing status (furnished/unfurnished)

        **Community Amenities:**
        - Pool facilities (private/shared, kids pool)
        - Fitness facilities (24-hour fitness center)
        - Recreational amenities (barbecue areas, tennis courts, etc.)
        - Security features (controlled access, gated entry, doorman)
        - Technology amenities (cable-ready, high-speed internet)
        - Pet facilities (dog park, pet spa)
        - Outdoor spaces (community garden, BBQ patio)
        - Business facilities (mail room, business center)

        **Parking Information:**
        - Available parking types (garage, covered, guest parking)
        - Special parking features (free snow removal)

        **Pet Policy:**
        - Allowed pet types (cats, small dogs, large dogs, etc.)
        - Breed restrictions (complete list of restricted breeds)
        - Pet fees: deposit amount, one-time fee, monthly rent
        - Pet limits and weight restrictions

        **Utilities:**
        - What's included (water, sewage, pest control, etc.)
        - What residents pay for (gas, electricity, cable, internet, garbage)
        - Specific fees (pest control fee amount)

        **Leasing Information:**
        - Application fee amount
        - Application instructions and process
        - Available lease terms (3, 6, 9, 12 months)
        - Lease break penalty details (notice period, fee amount)
        - Security deposit amount
        - Current leasing specials and promotions

        **Affordable Housing:**
        - Program participation details
        - Accepted programs (Section 8, tax credit)
        - Income limits and occupancy requirements

        **Additional Information:**
        - Social media and website links
        - Video content availability
        - Key selling points organized by category (community, location, units)
        - Special discounts (resident discounts, employer partnerships)
        - Preferred employer discount programs

        This tool provides all property information in a single, well-formatted response that agents can easily parse and present to users.
        """,
)
async def get_property_overview(
    property_id: Annotated[
        int,
        Field(description="Unique identifier for the property in Knock CRM to retrieve the overview for."),
    ],
    renter_type: Annotated[
        Literal["prospect", "applicant", "resident"],
        Field(
            description=(
                "Type of renter for whom the summary is generated. "
                "The value can only be one of 'prospect', 'resident', or 'applicant'."
            )
        ),
    ],
) -> dict | None:
    summary = """
    **Apartment Complex:** Cassidy South

    **Address:** 1401 n custer rd, McKinney, TX 75072

    **Neighborhood:** Canyon Creek

    **Description:** Experience the exceptional lifestyle at Cassidy South Apartments in Richardson, Texas. Our modern, pet-friendly bedroom layouts offer elegant finishes and features that match your on-the-go lifestyle. Perfectly positioned, stylishly appointed, and upscale in experience, Cassidy South Apartments redefines refined urban living. Located in the heart of Richardson, Cassidy South Apartment homes feature Luxury Accents, Walk-In Closets, Modern Kitchen Appliances, Eco-Friendly Washer & Dryer, and Balconies in most units.

    **Contact:** 253-802-8629, Email available
    
    **Towing Service:** Katie's Towing at 972-820-8000

    **Office Hours:**
    *   Monday: 9:00 AM - 5:00 PM
    *   Tuesday: 8:00 AM - 5:00 PM
    *   Wednesday: 9:00 AM - 6:00 PM
    *   Thursday: 8:00 AM - 5:00 PM
    *   Friday: Closed
    *   Saturday: Closed
    *   Sunday: Closed

    **Year Built:** 2023

    **Number of Units:** 250

    **Apartment Features:**
    *   Dishwasher
    *   Refrigerator
    *   Garbage disposal
    *   Microwave
    *   Range/Oven
    *   Hardwood floor
    *   Non-Smoking Property
    *   Quartz countertops
    *   Eco-Friendly Washer & Dryer
    *   Original Brick Accents
    *   Walk-In Closets
    *   Modern Kitchen Appliances
    *   Balconies in most units
    *   Unfurnished
    *   In Unit Laundry

    **Community Amenities:**
    *   Private pool
    *   Balcony/patio
    *   Deck
    *   Heat: forced air
    *   Central AC
    *   Air conditioning
    *   Ceiling fans
    *   Fitness center
    *   Barbecue
    *   Golf course
    *   Controlled access
    *   Gated entry
    *   Doorman
    *   Cable-ready
    *   High-speed internet
    *   Onsite Dog Park
    *   Community Garden
    *   Community BBQ Patio
    *   Sparkling Swimming Pool
    *   Tennis & Pickleball Courts
    *   24-Hour Security
    *   Mail room and business center
    *   Green Appliances
    *   Community Room

    **Parking:**
    *   Garage
    *   Covered
    *   Guests Parking Available
    *   Free Snow Removal

    **Pet Policy:**
    *   Cats and small dogs allowed. Large dogs are not allowed. Snakes are allowed.
    *   Breed Restrictions: We cannot accept the following: Akita; American Staffordshire Terrier; Bull Terrier; Chow; Doberman; German Shepherd; Pitbull; Presa Canario; Rottweiler; Wolf Hybrids.
    *   Pet Deposit: $200
    *   Pet Fee: $100
    *   Pet Rent: $25/month

    **Utilities:**
    *   Water: Included
    *   Sewage: Included
    *   Garbage: Resident responsible
    *   Gas: Resident responsible
    *   Electricity: Resident responsible
    *   Cable: Resident responsible
    *   Internet: Resident responsible
    *   Pest Control Fee of $2 per month: Included

    **Leasing Information:**
    *   Application Fee: $30
    *   Application Instructions: Appy to the lease now link at the top of our property website.
    *   Lease Terms: 3, 6, 9, and 12 month lease terms available.
    *   Lease Break Penalty: Lease breaks require a 60 notice to vacate, plus a fee equivalent to 2 months of rent.
    *   Security Deposit: $200
    *   Leasing Special: Our current special is $500 off your first month's rent.

    **Affordable Housing:**
    *   We do participate in a few affordable housing programs. Section 8 vouchers are accepted, we also allow a certain percentage of our apartments to be tax credit. There are maximum income limits and occupancy requirements that apply.
    *   Programs: Section 8 accepted

    **Social:**
    *   Website: http://www.cooperstation.com.g5static.com/

    **Videos:**
    *   https://www.youtube.com/cooperstation

    **Key Selling Points:**
    *   Community: You will love our pet park and pet spa! We offer a state of the art fitness center open 24 hours a day to our residents. Relax and dive into our beautiful pool or grill up a feast poolside.
    *   Location: Our community is within walking distance to Prairie Creek Park. We are centrally located close to shops, restaurants, and parks. Cassidy South Apartments has easy access to I-75 and is located near a local bus route.
    *   Units: We offer expansive walk in closets in all of our homes. Beautiful modern kitchens with quartz countertops. Luxurious bathrooms with soaker tubs, and separate showers.

    **Additional Notes:**
    *   We offer a 10% resident discount at Lonestar Coffee Co.
    *   Preferred employer discounts include: Lennox International, Amazon, CBRE Group, Honeywell, CVS Health, Capital One

    """
    return {"property_id": property_id, "summary": summary}


@mcp.tool()
async def get_property_data(property_id: str) -> str:
    """Fetch marketing information about a property.

    Examples of property data:
        - Office hours
        - Pet policies
        - Address and phone number


    Args:
        property_id: property id
    Returns:
        Marketing description of the property
    """
    return """
    **Property Overview**

    Experience the exceptional lifestyle at Cassidy South Apartments in Richardson, Texas. Our modern, pet-friendly bedroom layouts offer elegant finishes and features that match your on-the-go lifestyle. Perfectly positioned, stylishly appointed, and upscale in experience, Cassidy South Apartments redefines refined urban living. Located in the heart of Richardson, Cassidy South Apartment homes feature Luxury Accents, Walk-In Closets, Modern Kitchen Appliances, Eco-Friendly Washer & Dryer, and Balconies in most units.

    **Property Type**

    *   Cassidy South is an apartment building

    **Location**

    1401 n custer rd, McKinney, TX 75072
    Neighborhood: Canyon Creek

    **Contact Information**

    253-802-8629

    **Website**

    http://www.cassidysouth.com.g5static.com/

    **Office Hours**

    *   Monday: 9:00 AM - 5:00 PM
    *   Tuesday: 8:00 AM - 5:00 PM
    *   Wednesday: Closed
    *   Thursday: Closed
    *   Friday: Closed
    *   Saturday: Closed
    *   Sunday: Closed

    **Apartment Features**

    *   Hardwood floor
    *   Original Brick Accents
    *   Walk-In Closets
    *   Modern Kitchen Appliances
    *   Eco-Friendly Washer & Dryer
    *   Balconies in most units
    *   Green Appliances
    *   Unfurnished

    **Community Amenities**

    *   Private pool
    *   Balcony/patio
    *   Deck
    *   Heat: forced air
    *   Central AC
    *   Air conditioning
    *   Ceiling fans
    *   Fitness center
    *   Barbecue
    *   Golf course
    *   Controlled access
    *   Gated entry
    *   Doorman
    *   Cable-ready
    *   High-speed internet
    *   Dishwasher
    *   Refrigerator
    *   Garbage disposal
    *   Microwave
    *   Range/Oven
    *   In Unit Laundry
    *   Off-Street Parking
    *   Shared Pool
    *   Kids pool
    *   Onsite Dog Park
    *   Community Garden
    *   Community BBQ Patio
    *   Sparkling Swimming Pool
    *   Tennis & Pickleball Courts
    *   24-Hour Security
    *   Covered Parking
    *   Garages in Townhome Units
    *   Guests Parking Available
    *   Free Snow Removal
    *   Mail room and business center
    *   Non-Smoking Property
    *   Quartz countertops

    **Utilities**

    *   Water included
    *   Sewage included
    *   Pest Control Fee of $2 per month included
    *   Residents are responsible for Garbage, Gas, Electricity, Cable, and Internet.

    **Parking**

    *   Garage
    *   Covered
    *   Guest Parking

    **Pet Policy**

    *   Pet deposit: $200
    *   Pet fee: $100
    *   Pet rent: $25/month
    *   Two pets allowed per dwelling with a maximum weight of fifty pounds each.
    *   We cannot accept the following: Pit Bull Terriers, Staffordshire Terriers, Rottweilers, German Shepherds, Presa Canarios, Chow Chows, Doberman Pinchers, Akitas, Wolf-Hybrids, Mastiffs, Cane Corsos, Great Danes, Alaskan Malamutes and Siberian huskies.

    **Leasing Information**

    *   Application fee: $30
    *   Application instructions: Apply to the lease now link at the top of our property website.
    *   Lease breaks require a 60 notice to vacate, plus a fee equivalent to 2 months of rent.
    *   Security deposit: $200
    *   Lease lengths: 3, 6, 9, and 12 months
    *   Leasing special: Our current special is $500 off your first month's rent.
    *   Don't forget to mention 10% resident discount at Lonestar Coffee Co.

    **Fees**

    *   Admin Fee: $100
    *   Moveout cleaning fee: $150
    *   Cable & Internet: $100

    **Affordable Housing**

    *   We do participate in a few affordable housing programs. Section 8 vouchers are accepted, we also allow a certain percentage of our apartments to be tax credit. There are maximum income limits and occupancy requirements that apply.
    *   Programs: Section 8 accepted

    **Additional Notes**

    *   We offer a 10% resident discount at Lonestar Coffee Co.
    *   Preferred employer discounts include:
        *   Lennox International
        *   Amazon
        *   CBRE Group
        *   Honeywell
        *   CVS Health
        *   Capital One
    *   Online resident portal available.
    *   Blog with news and events about Cassidy South and Richardson, TX.

    **Key Selling Points**

    *   Community: You will love our pet park and pet spa! We offer a state of the art fitness center open 24 hours a day to our residents. Relax and dive into our beautiful pool or grill up a feast poolside.
    *   Location: Our community is within walking distance to Prairie Creek Park. We are centrally located close to shops, restaurants, and parks. Cassidy South Apartments has easy access to I-75 and is located near a local bus route.
    *   Units: We offer expansive walk in closets in all of our homes. Beautiful modern kitchens with quartz countertops. Luxurious bathrooms with soaker tubs, and separate showers.

    **Video**

    *   https://www.youtube.com/cassidysouth
    """


@mcp.tool()
async def schedule_tour(
    property_id: int,
    tour_date: Annotated[
        str,
        "Tour date and time prospect selected in the format YYYY-MM-DDTHH:MM:SS-HH:MM",
    ],
    first_name: Annotated[str, "First name of the prospect from existing guest card or ask prospect"],
    last_name: Annotated[str, "Last name of the prospect from existing guest card or ask prospect"],
    phone_number: Annotated[str, "Phone number of the prospect from existing guest card or ask prospect"],
    prospect_id: Optional[int] = None,
    preference_bedrooms: Annotated[
        Optional[int],
        "Number of bedrooms the prospect is interested in, can be None or a number between 0 and 4. If the value is invalid call with None",
    ] = None,
    preference_move_date: Annotated[Optional[str], "Preferred move-in date in the format: YYYY-MM-DD"] = None,
):
    """
    Schedules a tour for a prospect at a specified property.

    Args:
        property_id: The ID of the property where the tour is scheduled (e.g., "21521")
        tour_date: Tour date and time in the format YYYY-MM-DDTHH:MM:SS-HH:MM
        first_name: First name of the prospect
        last_name: Last name of the prospect
        phone_number: Phone number of the prospect
        prospect_id: Optional ID of an existing prospect (e.g., 97997)
        preference_bedrooms: Number of bedrooms the prospect is interested in (0-4, or None)
        preference_move_date: Preferred move-in date in the format YYYY-MM-DD
    Returns:
        The response from the API containing tour scheduling information
    """
    return f"I've scheduled your tour at {tour_date}."


@mcp.tool()
async def update_prospect(
    prospect_id: str,
    first_name: str | None = None,
    last_name: str | None = None,
    desired_move_in_date: str | None = None,
    bedrooms_number: int | None = None,
):
    """
    Updates prospect's guest card.

    Args:
        prospect_id: The ID of the prospect to update
        first_name: The prospect's first name
        last_name: The prospect's last name
        desired_move_in_date: The prospect's target move-in date
        bedrooms_number: Number of bedrooms (0 for studio)

    Returns:
        Prospect's updated guest card.
    """
    return "Prospect updated"


@mcp.tool()
async def update_prospect_consent_status(prospect_id: str, sms_consent: bool = False):
    """
    Updates the consent status for a specified prospect using the Knock API.

    This function sends an asynchronous HTTP PUT request to update the SMS
    consent status for the given prospect.

    Args:
        prospect_id: The unique identifier of the prospect whose SMS consent
            status is to be updated. (e.g., 97997)
        sms_consent: A boolean value indicating the updated SMS consent
            status for the prospect. Defaults to False.

    Returns:
        dict: The JSON response from the Knock API containing the updated
            prospect details.
    """
    return "Updated consent status"


@mcp.tool()
async def cancel_tour(appointment_id: str):
    """
    Cancels a tour appointment by making an asynchronous HTTP PUT request to the Knock API.

    The function uses the provided `appointment_id` to construct the API endpoint URL
    and sends a request to cancel the corresponding appointment. The function utilizes
    authentication headers retrieved from the KnockAuthService to authorize the request.
    It returns the JSON response received from the API.

    Args:
        appointment_id (str): The unique identifier of the appointment to be canceled. (e.g., 172904)

    Returns:
        dict: The JSON response returned by the Knock API, containing details of the
        cancellation result.

    Raises:
        httpx.RequestError: If there is a network-related issue during the request.
        httpx.HTTPStatusError: If an HTTP status code error is encountered during the
        request.
    """
    return "Canceled tour"


@mcp.tool()
async def get_pricing_and_availability(property_id: str):
    """Get information about pricing and availability

    Args:
      property_id: property ID
    Return:
      JSON response containing the pricing and availability details
    """
    return {
        "id": "8d3f9a9e-1f5d-4723-967e-0c4070f0ddaf",
        "createdAt": "2025-03-06T10:08:12.361",
        "modifiedAt": "2025-04-04T18:16:27.528",
        "deletedAt": None,
        "name": "Unit BthOb 5172",
        "price": "1899",
        "hidden": False,
        "occupied": False,
        "reserved": False,
        "leased": False,
        "noticeGiven": False,
        "available": True,
        "otherStatus": False,
        "availableOn": "2025-04-07",
        "buildingId": None,
        "levelId": None,
        "layoutId": None,
        "integrationId": "ff69f64a-27be-44fe-b152-cbf392fc69ed",
        "knockPrice": "1899",
        "type": None,
        "area": 1232,
        "bedrooms": 2,
        "bathrooms": 1,
        "rentMatrix": None,
        "vendorExtraAttributes": [],
        "layoutName": None,
        "propertyId": 21521,
        "buildingName": None,
    }


@mcp.tool(
    description="""
        Retrieve comprehensive prospect information from the guest card system including personal details, preferences, appointment history, and SMS consent status.

        RETURN FORMAT: Returns a dictionary with:
        - prospect: Main prospect object containing all prospect information
        - status_code: API response status ("ok" for success)

        INFORMATION AVAILABLE IN PROSPECT OBJECT:

        **Basic Prospect Info:**
        - id: Unique prospect identifier
        - created_time: When prospect was created
        - creation_source: How prospect was created (e.g., "self-schedule")
        - status: Current prospect status (e.g., "cancelled-renter")
        - is_active: Whether prospect is currently active
        - is_deleted: Deletion status
        - is_excluded: Whether prospect is excluded from follow-ups
        - is_waitlist: Whether prospect is on waitlist

        **Contact & Communication:**
        - assigned_manager_id: ID of assigned leasing manager
        - assigned_relay_phone: Phone number for relay communication
        - first_contact_type: How first contact was made
        - first_response_time: Timestamp of first response
        - last_contacted_time: Most recent contact timestamp
        - last_response_time: Most recent response from prospect
        - response_time_office_hours: Response time during business hours
        - follow_up_count: Number of follow-ups sent

        **Profile Information (profile object):**
        - first_name: Prospect's first name
        - last_name: Prospect's last name
        - email: Email address (if provided)
        - phone_number: Full phone number with country code
        - formatted_phone_number: Human-readable phone format
        - target_move_date: Desired move-in date
        - address_id: Associated address ID
        - income: Income information
        - pets: Pet information
        - co_tenants: Co-tenant details
        - phone: Detailed phone object with carrier info, SMS capability

        **Phone Details (phone object within profile):**
        - carrier_name: Phone carrier (e.g., "AT&T Wireless")
        - carrier_type: Type of carrier (e.g., "mobile")
        - can_receive_call: Whether phone can receive calls
        - can_receive_sms: Whether phone can receive SMS
        - country_code: Phone country code
        - national_format: Phone in national format

        **Preferences (preferences object):**
        - bedrooms: Preferred number of bedrooms (array)
        - max_price: Maximum price range
        - min_price: Minimum price range
        - preferred_lease_term_months: Desired lease length
        - amenities: Preferred amenities
        - neighborhoods: Preferred neighborhoods
        - number_of_occupants: Expected occupants
        - must_have: Must-have features
        - deal_breakers: Deal-breaking features

        **SMS Consent (sms_consent object):**
        - status: Consent status ("granted", "other", etc.)
        - has_consent: Boolean consent status
        - bypass_consent: Whether consent is bypassed
        - has_express_consent_override: Express consent override status
        - created_time: When consent was created
        - modified_time: When consent was last modified
        - note: Any notes about consent

        **Appointment History (events array):**
        Each event contains:
        - id: Appointment ID
        - event_type: Type of event ("appointment")
        - start_time: Appointment start time
        - end_time: Appointment end time
        - status: Appointment status ("confirmed", "cancelled", etc.)
        - tour_type: Type of tour
        - manager_id: Assigned manager for appointment
        - auto_accepted: Whether appointment was auto-accepted
        - shown_units: Units shown during appointment
        - source: Source of appointment booking

        **Loss Reasons (loss_reasons object):**
        Boolean flags for various loss reasons including:
        - Price-related: "Price too high"
        - Location-related: "Location - commute", "Location - noise", etc.
        - Property-related: "Amenities", "Parking", "Unit features", etc.
        - Qualification-related: "Disqualified - Credit", "Disqualified - Income", etc.
        - Timing-related: "Timing", "Not moving", "Looking to pre-lease"
        - Competition-related: "Rented elsewhere", "Purchased home/condo"

        **Property & Community Info:**
        - property_id: Associated property ID
        - community: Community information object
        - resource_id: Resource identifier
        - renter_id: Associated renter ID

        **AI & Integration Features:**
        - ai_email: AI email settings and availability
        - integrations: Array of integration connections
        - stored_custom_fields: Custom field data
        - export_failures: Any export failure information

        This tool provides complete prospect information for agents to understand prospect status, preferences, communication history, and appointment details.
    """
)
async def get_prospect_guest_card(prospect_id: str):
    """
    Get prospect guest card

    Args:
        prospect_id: prospect ID
    Returns:
        JSON response containing the prospect guest card
    """

    guest_card = {
        "prospect": {
            "ai_email": {
                "enabled": False,
                "togglable": False,
                "tooltip": "AIEmail is not active for this community. Please reach out to your Knock CRM account manager to discuss what AI can do for you!",
            },
            "assigned_manager_id": 242978,
            "assigned_relay_phone": "+18593282811",
            "business_time_to_first_response": "0:00:00",
            "community": {"id": "43c7911ee385bd9c"},
            "created_time": "2025-03-11T15:39:14.631260+00:00",
            "creation_source": "self-schedule",
            "developer_id": None,
            "disable_follow_ups": False,
            "disable_is_excluded": False,
            "disable_lost_status": False,
            "enable_cheatproof_engagement_score": False,
            "events": [
                {
                    "appointment_property_units": [],
                    "auto_accepted": True,
                    "created_time": "2025-03-11T15:39:15.356838+00:00",
                    "developer_id": None,
                    "end_time": "2025-03-12T13:30:00+00:00",
                    "event_time": "2025-03-12T13:00:00+00:00",
                    "event_type": "appointment",
                    "id": 172904,
                    "is_deleted": False,
                    "manager_id": 242978,
                    "modified_time": "2025-03-11T16:09:25.441225+00:00",
                    "origin": "knock",
                    "prospect_id": 97997,
                    "reference_info": None,
                    "reminder_id": "53ba651d-cb3f-4578-bac4-afb7a8d84b7b",
                    "request_id": None,
                    "resource_id": 795781,
                    "review_reminder_id": None,
                    "review_token": None,
                    "shown_units": [],
                    "sms_consent": {
                        "bypass_consent": False,
                        "created_time": "2025-03-11T15:39:14.882045+00:00",
                        "has_consent": True,
                        "has_express_consent_override": False,
                        "id": 92235,
                        "is_deleted": False,
                        "modified_time": "2025-05-01T03:50:02.048347+00:00",
                        "note": None,
                        "status": "granted",
                    },
                    "source": "Knock",
                    "start_time": "2025-03-12T13:00:00+00:00",
                    "status": "cancelled",
                    "tour_type": None,
                    "type": "request",
                    "uuid": "b821b886-d7ed-459e-9b79-b5d3630eb5b9",
                }
            ],
            "export_failures": [],
            "first_contact_response_time": 0,
            "first_contact_type": "knock-schedule",
            "first_response_time": "2025-03-11T15:39:15.420879+00:00",
            "follow_up_count": 0,
            "has_appointments": True,
            "has_call_recording": None,
            "has_facebook_messenger": False,
            "has_note": None,
            "id": 97997,
            "integrations": [],
            "is_active": True,
            "is_deleted": False,
            "is_excluded": True,
            "is_waitlist": False,
            "is_winback_suppressed": True,
            "last_contacted_time": "2025-05-02T19:43:26.822380+00:00",
            "last_relevant_time": "2025-05-02T19:43:26.822380+00:00",
            "last_response_time": None,
            "leased_date": None,
            "loss_reasons": {
                "Affordable Housing": False,
                "Amenities": False,
                "Appliances": False,
                "Concession": False,
                "Covid-19": False,
                "Did not explain": False,
                "Disqualified - Age": False,
                "Disqualified - Credit": False,
                "Disqualified - Criminal": False,
                "Disqualified - Income": False,
                "Disqualified - Pets": False,
                "Disqualified - Rental history": False,
                "Disqualified income - not meet rent/income ratio": False,
                "Disqualified income - over income": False,
                "Floorplan - layout": False,
                "Floorplan - size": False,
                "Gym": False,
                "Invalid contact information": False,
                "Lease length": False,
                "Lease modification": False,
                "Leased at Sister Community": False,
                "Location - commute": False,
                "Location - noise": False,
                "Location - schools": False,
                "Location - walkability": False,
                "Looking to pre-lease": False,
                "Moved in with family": False,
                "Moved out-of-state": False,
                "Mystery Shopper": False,
                "Needs Assisted Living": False,
                "Needs Higher Level of Care": False,
                "No Show": False,
                "No availability": False,
                "No dog park": False,
                "Not moving": False,
                "Not seeking individual leasing": False,
                "Parking": False,
                "Pets not allowed": False,
                "Price too high": False,
                "Purchased home/condo": False,
                "Referred to Sister Community": False,
                "Refused communication": False,
                "Rented elsewhere": False,
                "Timing": False,
                "Unit features": False,
                "Unresponsive": False,
                "View": False,
            },
            "modified_time": "2025-05-02T19:43:26.806489+00:00",
            "origin": "knock",
            "pms_created_time": None,
            "preferences": {
                "amenities": None,
                "bedrooms": ["1bd"],
                "created_by_type": "manager",
                "created_time": "2025-03-11T15:39:14.564091+00:00",
                "deal_breakers": None,
                "id": 154933,
                "is_deleted": False,
                "max_price": 0,
                "min_price": 0,
                "modified_time": "2025-05-02T19:42:09.154661+00:00",
                "must_have": None,
                "neighborhoods": None,
                "number_of_occupants": None,
                "preferred_layout_id": None,
                "preferred_lease_term_months": None,
                "preferred_property_floorplan_id": None,
                "preferred_property_unit_id": None,
                "preferred_unit_id": None,
                "preferrred_property_floorplan": None,
                "preferrred_property_unit": None,
            },
            "preferences_id": 154933,
            "profile": {
                "address_id": None,
                "bio": None,
                "co_tenants": None,
                "created_by_type": "manager",
                "created_time": "2025-03-11T15:39:14.557705+00:00",
                "email": None,
                "first_name": "Marcus",
                "formatted_phone_number": "(650) 224-8101",
                "id": 772374,
                "id_verified": None,
                "id_verify_report_url": None,
                "income": None,
                "is_criminal": None,
                "is_deleted": False,
                "is_winback_enabled": True,
                "last_name": "Allen",
                "modified_time": "2025-05-02T19:42:09.154661+00:00",
                "pets": None,
                "phone": {
                    "caller_id_name": "",
                    "caller_id_type": None,
                    "can_receive_call": True,
                    "can_receive_sms": True,
                    "carrier_name": "AT&T Wireless",
                    "carrier_type": "mobile",
                    "country_code": "US",
                    "created_time": "2025-03-17T21:25:04.438502+00:00",
                    "id": 162734,
                    "is_deleted": False,
                    "modified_time": None,
                    "national_format": "(650) 224-8101",
                    "phone_number": "+16502248101",
                },
                "phone_id": 162734,
                "phone_number": "+16502248101",
                "photo": "https://alpha-knockphotos.s3.amazonaws.com/common/assets/profile_placeholder.png",
                "selfiescan_pdf_key": None,
                "target_move_date": "2025-05-04",
                "verification_method": None,
                "was_evicted": None,
            },
            "profile_id": 772374,
            "property": {
                "created_time": "2023-08-16T21:09:43.447551+00:00",
                "id": 21513,
                "is_deleted": False,
                "leasing_team_id": 2101,
                "modified_time": "2025-04-24T12:00:02.127308+00:00",
                "owning_manager_id": 242978,
                "public_id": "8a956543",
                "resource_id": 86111,
                "timezone": "America/Chicago",
                "type": "multi-family",
            },
            "property_": {
                "created_time": "2023-08-16T21:09:43.447551+00:00",
                "id": 21513,
                "is_deleted": False,
                "leasing_team_id": 2101,
                "modified_time": "2025-04-24T12:00:02.127308+00:00",
                "owning_manager_id": 242978,
                "public_id": "8a956543",
                "resource_id": 86111,
                "timezone": "America/Chicago",
                "type": "multi-family",
            },
            "property_id": 21513,
            "renter_id": 274645,
            "resource_id": 795780,
            "response_time_office_hours": 0,
            "sms_consent": {
                "bypass_consent": False,
                "created_time": "2025-03-11T15:39:14.882045+00:00",
                "has_consent": True,
                "has_express_consent_override": False,
                "id": 92235,
                "is_deleted": False,
                "modified_time": "2025-05-01T03:50:02.048347+00:00",
                "note": None,
                "status": "granted",
            },
            "sms_consent_id": 92235,
            "source": "Knock",
            "status": "cancelled-renter",
            "stored_custom_fields": {},
            "stream_id": "TOV5MEBL-1741707555",
            "time_to_first_response": "0:00:00",
            "todo_status": {
                "color": "green",
                "explanation": "not a prospect",
                "liveness": "stale",
                "urgency": 100,
            },
        },
        "status_code": "ok",
    }

    return guest_card


@mcp.tool()
async def get_property_available_timeslots(
    property_id: Annotated[str, "The property ID"],
    date_from: Annotated[datetime, "Date from which to get available timeslots in the format"],
    date_to: Annotated[datetime, "Date to which to get available timeslots in the format"],
):
    """
    Get available timeslots for a property for the given date range.

    Days in `property_available_times` followed by a time offset and list of available times represented in military hours.
    """
    return {
        "property_available_times": {
            "Friday, June 27, 2025": {
                "offset": "-04:00",
                "times": "1200, 1230, 300, 1330, 1400, 1430, 1500, 1530, 1600, 1630, 1700, 1730, 1800, 1830, 1900, 1930, 2000, 2030, 2100, 2130, 2200, 2230",
            },
            "Monday, June 30, 2025": {
                "offset": "-04:00",
                "times": "0800, 0830, 0900, 0930, 1000, 1030, 1100, 1130, 1200, 1230, 1300, 1330, 1400, 1430, 1500, 1530, 1600, 1630, 1700, 1730, 1800, 1830, 1900, 1930, 2000, 2030, 2100, 2130, 2200, 2230",
            },
            "Tuesday, July 1, 2025": {
                "offset": "-04:00",
                "times": "0800, 0830, 0900, 0930, 1000, 1030, 1100, 1130, 1200, 1230, 1300, 1330, 1400, 1430, 1500, 1530, 1600, 1630, 1700, 1730, 1800, 1830, 1900, 1930, 2000, 2030, 2100, 2130, 2200, 2230",
            },
        },
        "reviewable_times": None,
        "self_schedule": True,
    }


@mcp.tool()
async def get_appointments(renter_id: str = None):
    """
    Fetches a list of appointments associated with a specific renter ID from the KnockCRM API.

    The function interacts with the KnockCRM API to retrieve appointment data. It uses an
    async context manager to manage the lifecycle of the KnockDataService object, ensuring
    proper resource cleanup after the API interaction is complete. If a renter ID is provided,
    appointments specific to that renter are fetched.

    Note: The renter_Id is different from the prospect_id.

    Args:
        renter_id: The ID of the renter whose appointments are being fetched. Defaults
            to None if no specific renter ID is specified. (e.g., 274645)

    Returns:
        A JSON-like dictionary containing the appointment data retrieved from the
        KnockCRM API.

    Raises:
        Any exceptions encountered during the API call or the data fetching process
        within the KnockDataService context manager.
    """
    return {
        "appointments": [
            {
                "appointment_property_units": [],
                "auto_accepted": True,
                "created_time": "2025-03-11T15:39:15.356838+00:00",
                "developer_id": None,
                "end_time": "2025-03-12T13:30:00+00:00",
                "id": 172904,
                "is_deleted": False,
                "manager_id": 242978,
                "modified_time": "2025-03-11T16:09:25.441225+00:00",
                "origin": "knock",
                "prospect_id": 97997,
                "reference_info": None,
                "reminder_id": "53ba651d-cb3f-4578-bac4-afb7a8d84b7b",
                "request_id": None,
                "resource_id": 795781,
                "review_reminder_id": None,
                "review_token": None,
                "source": "Knock",
                "start_time": "2025-03-12T13:00:00+00:00",
                "status": "cancelled",
                "tour_type": None,
                "type": "request",
                "uuid": "b821b886-d7ed-459e-9b79-b5d3630eb5b9",
            },
            {
                "appointment_property_units": [],
                "auto_accepted": True,
                "created_time": "2025-03-13T16:46:41.493323+00:00",
                "developer_id": None,
                "end_time": "2025-03-13T23:30:00+00:00",
                "id": 172983,
                "is_deleted": False,
                "manager_id": 237824,
                "modified_time": "2025-03-13T16:46:41.593144+00:00",
                "origin": "knock",
                "prospect_id": 98197,
                "reference_info": None,
                "reminder_id": "fac0e8d1-55d7-48f2-932e-6e0928ead950",
                "request_id": None,
                "resource_id": 796636,
                "review_reminder_id": None,
                "review_token": None,
                "source": "Knock",
                "start_time": "2025-03-13T23:00:00+00:00",
                "status": "confirmed",
                "tour_type": None,
                "type": "request",
                "uuid": "5767fec5-cc18-49c6-97be-d5cff61ff799",
            },
        ],
        "requests": [],
        "status_code": "ok",
    }


@mcp.tool()
async def get_property_available_times(property_id: str):
    """Get available tour times for a property.
    Args:
        property_id: property ID
    Returns:
        JSON response containing list of available times
    """
    return {
        "available_times": {
            "acceptable_times": [],
            "allow_non_preferred_times": None,
            "preferred_times": [],
            "preferred_times_instant_book": None,
            "reviewable_times": [
                "2025-05-02T18:00:00-05:00",
                "2025-05-02T18:30:00-05:00",
                "2025-05-02T19:00:00-05:00",
                "2025-05-02T19:30:00-05:00",
                "2025-05-02T20:00:00-05:00",
                "2025-05-02T20:30:00-05:00",
                "2025-05-02T21:00:00-05:00",
                "2025-05-02T21:30:00-05:00",
                "2025-05-03T07:00:00-05:00",
                "2025-05-03T07:30:00-05:00",
                "2025-05-03T08:00:00-05:00",
                "2025-05-03T08:30:00-05:00",
                "2025-05-03T12:00:00-05:00",
                "2025-05-03T12:30:00-05:00",
                "2025-05-03T14:30:00-05:00",
                "2025-05-03T15:00:00-05:00",
                "2025-05-03T15:30:00-05:00",
                "2025-05-03T16:00:00-05:00",
                "2025-05-03T16:30:00-05:00",
                "2025-05-03T17:00:00-05:00",
                "2025-05-03T17:30:00-05:00",
                "2025-05-03T18:00:00-05:00",
                "2025-05-03T18:30:00-05:00",
                "2025-05-03T19:00:00-05:00",
                "2025-05-03T19:30:00-05:00",
                "2025-05-03T20:00:00-05:00",
                "2025-05-03T20:30:00-05:00",
                "2025-05-03T21:00:00-05:00",
                "2025-05-03T21:30:00-05:00",
                "2025-05-04T07:00:00-05:00",
                "2025-05-04T07:30:00-05:00",
                "2025-05-04T08:00:00-05:00",
                "2025-05-04T08:30:00-05:00",
                "2025-05-04T09:00:00-05:00",
                "2025-05-04T09:30:00-05:00",
                "2025-05-04T10:00:00-05:00",
                "2025-05-04T10:30:00-05:00",
                "2025-05-04T11:00:00-05:00",
                "2025-05-04T11:30:00-05:00",
                "2025-05-04T12:00:00-05:00",
                "2025-05-04T12:30:00-05:00",
                "2025-05-04T13:00:00-05:00",
                "2025-05-04T13:30:00-05:00",
                "2025-05-04T14:00:00-05:00",
                "2025-05-04T14:30:00-05:00",
                "2025-05-04T15:00:00-05:00",
                "2025-05-04T15:30:00-05:00",
                "2025-05-04T16:00:00-05:00",
                "2025-05-04T16:30:00-05:00",
                "2025-05-04T17:00:00-05:00",
                "2025-05-04T17:30:00-05:00",
                "2025-05-04T18:00:00-05:00",
                "2025-05-04T18:30:00-05:00",
                "2025-05-04T19:00:00-05:00",
                "2025-05-04T19:30:00-05:00",
                "2025-05-04T20:00:00-05:00",
                "2025-05-04T20:30:00-05:00",
                "2025-05-04T21:00:00-05:00",
                "2025-05-04T21:30:00-05:00",
                "2025-05-05T07:00:00-05:00",
                "2025-05-05T07:30:00-05:00",
                "2025-05-05T08:00:00-05:00",
                "2025-05-05T08:30:00-05:00",
                "2025-05-05T09:00:00-05:00",
                "2025-05-05T09:30:00-05:00",
                "2025-05-05T10:00:00-05:00",
                "2025-05-05T10:30:00-05:00",
                "2025-05-05T11:00:00-05:00",
                "2025-05-05T11:30:00-05:00",
                "2025-05-05T12:00:00-05:00",
                "2025-05-05T12:30:00-05:00",
                "2025-05-05T13:00:00-05:00",
                "2025-05-05T13:30:00-05:00",
                "2025-05-05T14:00:00-05:00",
                "2025-05-05T14:30:00-05:00",
                "2025-05-05T15:00:00-05:00",
                "2025-05-05T15:30:00-05:00",
                "2025-05-05T16:00:00-05:00",
                "2025-05-05T16:30:00-05:00",
                "2025-05-05T17:00:00-05:00",
                "2025-05-05T17:30:00-05:00",
                "2025-05-05T18:00:00-05:00",
                "2025-05-05T18:30:00-05:00",
                "2025-05-05T19:00:00-05:00",
                "2025-05-05T19:30:00-05:00",
                "2025-05-05T20:00:00-05:00",
                "2025-05-05T20:30:00-05:00",
                "2025-05-05T21:00:00-05:00",
                "2025-05-05T21:30:00-05:00",
                "2025-05-06T07:00:00-05:00",
                "2025-05-06T07:30:00-05:00",
                "2025-05-06T08:00:00-05:00",
                "2025-05-06T08:30:00-05:00",
                "2025-05-06T09:00:00-05:00",
                "2025-05-06T09:30:00-05:00",
                "2025-05-06T10:00:00-05:00",
                "2025-05-06T10:30:00-05:00",
                "2025-05-06T11:00:00-05:00",
                "2025-05-06T11:30:00-05:00",
                "2025-05-06T12:00:00-05:00",
                "2025-05-06T12:30:00-05:00",
                "2025-05-06T13:00:00-05:00",
                "2025-05-06T13:30:00-05:00",
                "2025-05-06T14:00:00-05:00",
                "2025-05-06T14:30:00-05:00",
                "2025-05-06T15:00:00-05:00",
                "2025-05-06T15:30:00-05:00",
                "2025-05-06T16:00:00-05:00",
                "2025-05-06T16:30:00-05:00",
                "2025-05-06T17:00:00-05:00",
                "2025-05-06T17:30:00-05:00",
                "2025-05-06T18:00:00-05:00",
                "2025-05-06T18:30:00-05:00",
                "2025-05-06T19:00:00-05:00",
                "2025-05-06T19:30:00-05:00",
                "2025-05-06T20:00:00-05:00",
                "2025-05-06T20:30:00-05:00",
                "2025-05-06T21:00:00-05:00",
                "2025-05-06T21:30:00-05:00",
                "2025-05-07T07:00:00-05:00",
                "2025-05-07T07:30:00-05:00",
                "2025-05-07T08:00:00-05:00",
                "2025-05-07T08:30:00-05:00",
                "2025-05-07T09:00:00-05:00",
                "2025-05-07T09:30:00-05:00",
                "2025-05-07T10:00:00-05:00",
                "2025-05-07T10:30:00-05:00",
                "2025-05-07T11:00:00-05:00",
                "2025-05-07T11:30:00-05:00",
                "2025-05-07T12:00:00-05:00",
                "2025-05-07T12:30:00-05:00",
                "2025-05-07T13:00:00-05:00",
                "2025-05-07T13:30:00-05:00",
                "2025-05-07T14:00:00-05:00",
                "2025-05-07T14:30:00-05:00",
                "2025-05-07T15:00:00-05:00",
                "2025-05-07T15:30:00-05:00",
                "2025-05-07T16:00:00-05:00",
                "2025-05-07T16:30:00-05:00",
                "2025-05-07T17:00:00-05:00",
                "2025-05-07T17:30:00-05:00",
                "2025-05-07T18:00:00-05:00",
                "2025-05-07T18:30:00-05:00",
                "2025-05-07T19:00:00-05:00",
                "2025-05-07T19:30:00-05:00",
                "2025-05-07T20:00:00-05:00",
                "2025-05-07T20:30:00-05:00",
                "2025-05-07T21:00:00-05:00",
                "2025-05-07T21:30:00-05:00",
                "2025-05-08T07:00:00-05:00",
                "2025-05-08T07:30:00-05:00",
                "2025-05-08T08:00:00-05:00",
                "2025-05-08T08:30:00-05:00",
                "2025-05-08T09:00:00-05:00",
                "2025-05-08T09:30:00-05:00",
                "2025-05-08T10:00:00-05:00",
                "2025-05-08T10:30:00-05:00",
                "2025-05-08T11:00:00-05:00",
                "2025-05-08T11:30:00-05:00",
                "2025-05-08T12:00:00-05:00",
                "2025-05-08T12:30:00-05:00",
                "2025-05-08T13:00:00-05:00",
                "2025-05-08T13:30:00-05:00",
                "2025-05-08T14:00:00-05:00",
                "2025-05-08T14:30:00-05:00",
                "2025-05-08T15:00:00-05:00",
                "2025-05-08T15:30:00-05:00",
                "2025-05-08T16:00:00-05:00",
                "2025-05-08T16:30:00-05:00",
                "2025-05-08T17:00:00-05:00",
                "2025-05-08T17:30:00-05:00",
                "2025-05-08T18:00:00-05:00",
                "2025-05-08T18:30:00-05:00",
                "2025-05-08T19:00:00-05:00",
                "2025-05-08T19:30:00-05:00",
                "2025-05-08T20:00:00-05:00",
                "2025-05-08T20:30:00-05:00",
                "2025-05-08T21:00:00-05:00",
                "2025-05-08T21:30:00-05:00",
                "2025-05-09T07:00:00-05:00",
                "2025-05-09T07:30:00-05:00",
                "2025-05-09T08:00:00-05:00",
                "2025-05-09T08:30:00-05:00",
                "2025-05-09T09:00:00-05:00",
                "2025-05-09T09:30:00-05:00",
                "2025-05-09T10:00:00-05:00",
                "2025-05-09T10:30:00-05:00",
                "2025-05-09T11:00:00-05:00",
                "2025-05-09T11:30:00-05:00",
                "2025-05-09T12:00:00-05:00",
                "2025-05-09T12:30:00-05:00",
                "2025-05-09T13:00:00-05:00",
                "2025-05-09T13:30:00-05:00",
                "2025-05-09T14:00:00-05:00",
                "2025-05-09T14:30:00-05:00",
                "2025-05-09T15:00:00-05:00",
                "2025-05-09T15:30:00-05:00",
                "2025-05-09T16:00:00-05:00",
                "2025-05-09T16:30:00-05:00",
                "2025-05-09T17:00:00-05:00",
                "2025-05-09T17:30:00-05:00",
                "2025-05-09T18:00:00-05:00",
                "2025-05-09T18:30:00-05:00",
                "2025-05-09T19:00:00-05:00",
                "2025-05-09T19:30:00-05:00",
                "2025-05-09T20:00:00-05:00",
                "2025-05-09T20:30:00-05:00",
                "2025-05-09T21:00:00-05:00",
                "2025-05-09T21:30:00-05:00",
            ],
        },
        "status_code": "ok",
    }


# RESIDENT MCP TOOLS
@mcp.tool()
async def get_rent_information(
    company_id: int | str, property_id: int | str, resident_household_id: int | str, resident_member_id: int | str
):
    """
    Fetch rent information for a specific resident from both Payments SOAP API and Ledger API

    This tool calls two APIs in parallel:
    1. Payments SOAP API - for balance, pending_balance, and rent amount
    2. Ledger API - for rent_due_date from scheduled billing

    Args:
        company_id (str): The ID of the company for which rent information is being fetched
        property_id (str): The ID of the property for which rent information is being fetched
        resident_household_id (str): The resident household ID (ReshId)
        resident_member_id (str): The resident member ID (ResmId) - identifies the individual within the household

    Returns:
        dict: The parsed response containing the rent information for the specified resident
    Example:
        Input: company_id=4426167, property_id=4426174, resident_household_id=926, resident_member_id=1002
        Output: {
            "current_balance": "$6501.66",
            "past_due_balance": "$0.00",
            "rent": "$3950.00",
            "rent_due_date": "2026-01-01T00:00:00+00:00"
        }

    """
    return {
        "current_balance": "$123.45",
        "past_due_balance": "$0.00",
        "rent": "$1,899.00",
        "rent_due_date": generate_datetime_string(6, 0, 0, "+00:00"),
    }


@mcp.tool()
async def get_fas_account_statement(
    company_id: int | str,
    property_id: int | str,
    resident_household_id: int | str,
    resident_member_id: int | str,
    lease_id: int | str,
):
    """
    Fetch the FAS (financial account statement) for a former resident.

    Args:
        company_id (str): The ID of the company
        property_id (str): The ID of the property
        resident_household_id (str): The resident household ID (ReshId)
        resident_member_id (str): The resident member ID (ResmId) - identifies the individual within the household
        lease_id (str): The resident's lease ID

    Returns:
        dict: The parsed response containing the FAS data, including resident information, the closed-statement date (fasClosedSystemDate), and the final balance.
    """

    return {
        "structuredContent": {
            "fas_account_statement": {
                "residentInformation": {
                    "leaseEndDate": "03/31/2025",
                    "moveinDate": "04/01/2024",
                    "moveoutDate": None,
                    "noticeGivenDate": "12/01/2025",
                    "reasonForMovingOut": "Bought home / condo",
                    "leaseID": "225",
                    "fullName": "Debbie Wilcox",
                    "unitNumber": "101",
                    "deposit": "1,800.00",
                    "leaseStart": "04/01/2024",
                    "status": "Current resident  notice given - move out",
                    "building": "1",
                    "balance": "15,870.10",
                    "forwardingAddress": {
                        "address": "102 W 93rd St Apt 101",
                        "city": "New York",
                        "stateCode": "NY",
                        "zip": "10025-7530",
                    },
                },
                "ledgerAccountAtMoveOut": [],
                "depositActivities": [],
                "additionalCharges": [],
                "fasDetails": {
                    "modifyDate": "03/16/2026",
                    "fasBalance": "1733.00",
                    "fasPreparedBy": "Lee, Ira",
                    "fasClosedSystemDate": "04/16/2025",
                },
            },
            "status_code": 200,
            "total_records": 1,
        },
    }


@mcp.tool()
async def get_resident_autopay_and_transactions(
    resm_id: int | str,
    company_id: int | str,
    property_id: int | str,
    resh_id: int | str,
    lease_id: int | str,
):
    """
    Fetch a resident's autopay configuration, outstanding balance, and transaction history.

    Args:
        company_id (str): The ID of the company
        property_id (str): The ID of the property
        resh_id (str): The resident household ID (ReshId)
        resm_id (str): The resident member ID (ResmId) - identifies the individual within the household
        lease_id (str): The resident's lease ID

    Returns:
        dict: The parsed response containing autopay_settings, outstanding_balance, and a list of transactions (charges and payments) within the transaction_date_range.
    """

    return {
        "structuredContent": {
            "transaction_date_range": {"start_date": "01/30/2025", "end_date": "04/20/2026"},
            "resident_sub_journal": True,
            "autopay_settings": [
                {
                    "description": "Preauthorized Payment",
                    "type": "Open Balance",
                    "frequency": "Monthly",
                    "amount": "900.00",
                    "start_date": generate_date_mmddyyyy(months=-1),
                    "end_date": generate_date_mmddyyyy(months=11),
                    "next_due_date": generate_date_mmddyyyy(months=8, days=18),
                    "status": "Enabled",
                },
                {
                    "description": "Preauthorized Payment",
                    "type": "Fixed Amount",
                    "frequency": "Semi-monthly",
                    "amount": "600.00",
                    "start_date": generate_date_mmddyyyy(months=-1),
                    "end_date": generate_date_mmddyyyy(months=6),
                    "next_due_date": generate_date_mmddyyyy(months=7, days=13),
                    "status": "Enabled",
                },
            ],
            "outstanding_balance": "15830.10",
            "transactions": [
                {
                    "ledger_id": 50600,
                    "major_group": "C",
                    "charge_amount": "999.99",
                    "change_mate": "0",
                    "transaction_code": "LATEFEE",
                    "credit_amount": "",
                    "date": generate_date_mmddyyyy(days=-1),
                    "transaction_desc": "Rent late fee",
                },
                {
                    "ledger_id": 323687,
                    "major_group": "C",
                    "charge_amount": "50.00",
                    "change_mate": "0",
                    "transaction_code": "PARKING",
                    "credit_amount": "",
                    "date": generate_date_mmddyyyy(days=0),
                    "transaction_desc": "Reserved Or Covered Parking Charges",
                },
                {
                    "ledger_id": 50600,
                    "major_group": "C",
                    "charge_amount": "3166.02",
                    "change_mate": "0",
                    "transaction_code": "RENT",
                    "credit_amount": "",
                    "date": generate_date_mmddyyyy(days=-1),
                    "transaction_desc": "Rent",
                },
                {
                    "ledger_id": 50600,
                    "major_group": "C",
                    "charge_amount": "333",
                    "change_mate": "0",
                    "transaction_code": "NSF",
                    "credit_amount": "",
                    "date": generate_date_mmddyyyy(days=-1),
                    "transaction_desc": "Non sufficient funds charge",
                },
                {
                    "ledger_id": 50600,
                    "major_group": "C",
                    "charge_amount": "444",
                    "change_mate": "0",
                    "transaction_code": "NSF",
                    "credit_amount": "",
                    "date": generate_date_mmddyyyy(days=0),
                    "transaction_desc": "Non sufficient funds charge",
                },
                {
                    "ledger_id": 50495,
                    "major_group": "C",
                    "charge_amount": "3166.02",
                    "change_mate": "0",
                    "transaction_code": "RENT",
                    "credit_amount": "",
                    "date": generate_date_mmddyyyy(months=-1),
                    "transaction_desc": "Rent",
                },
                {
                    "ledger_id": 50384,
                    "major_group": "C",
                    "charge_amount": "3166.02",
                    "change_mate": "0",
                    "transaction_code": "RENT",
                    "credit_amount": "",
                    "date": generate_date_mmddyyyy(months=-2),
                    "transaction_desc": "Rent",
                },
            ],
        },
    }


@mcp.tool()
async def get_property_details(
    company_id: int | str,
    property_id: int | str,
):
    """
    Fetch OneSite property details, including the late fee day.

    Args:
        company_id (str): The ID of the company
        property_id (str): The ID of the property

    Returns:
        dict: The parsed response containing property details such as property_name and lateFeeDay.
    """

    return {
        "structuredContent": {"property_name": "AvanPTrails of Woodlake", "lateFeeDay": "15"},
    }


@mcp.tool(
    name="get_custom_reminders",
    title="Get Custom Reminders",
    description=(
        "Retrieve all active custom reminders for a resident that are scheduled for today or a future date. "
        "Use this tool when you need to check upcoming reminders set for a specific resident at a given property. "
        "Only non-deleted reminders with a reminder date on or after today's date are returned. "
        "Past reminders and soft-deleted records are excluded. "
        "Results are ordered by reminder date ascending so the nearest reminder appears first."
    ),
)
async def get_custom_reminders(
    company_id: Annotated[int, Field(description="The PMC ID — identifies the property management company.")],
    property_id: Annotated[int, Field(description="The site ID — identifies the property within the company.")],
    resh_id: Annotated[
        int, Field(description="The resident history ID — identifies the specific resident household row.")
    ],
) -> dict:
    return {"custom_reminders": [], "total_reminders": 0}


@mcp.tool(
    name="manage_custom_reminders",
    title="Manage Custom Reminders",
    description=(
        "Create, update, or soft-delete a custom reminder for a resident. "
        "Supports three actions: 'insert' creates a new reminder, 'update' modifies an existing reminder "
        "(replacing the reminder_date and context for the resident's single active record of the matching "
        "PTP: / REMINDER: type), and 'delete' soft-deletes a reminder by reminder_date. "
        "All dates must be in YYYY-MM-DD or MM/DD/YYYY format and must be strictly in the future."
    ),
)
async def manage_custom_reminders(
    company_id: Annotated[int, Field(description="The PMC ID — identifies the property management company.")],
    property_id: Annotated[int, Field(description="The site ID — identifies the property within the company.")],
    resh_id: Annotated[
        int, Field(description="The resident history ID — identifies the specific resident household row.")
    ],
    reminder_date: Annotated[
        str, Field(description="The reminder date in YYYY-MM-DD or MM/DD/YYYY format. Must be strictly future.")
    ],
    action: Annotated[Literal["insert", "update", "delete"], Field(description="The operation to perform.")],
    reminder_context: Annotated[
        Optional[str], Field(description="Free-text notes or reason for the reminder.")
    ] = None,
    new_reminder_date: Annotated[
        Optional[str],
        Field(
            description=(
                "For action='update', the new reminder date in YYYY-MM-DD or MM/DD/YYYY format, "
                "or an empty string if the date is unchanged. Ignored for 'insert' and 'delete'."
            ),
        ),
    ] = "",
) -> dict:
    return {"action": action, "affected_rows": 1}


@mcp.tool(
    description="""
        Retrieve detailed lease information for a specific resident including lease dates, unit details, and occupant information.

        RETURN FORMAT: Returns a dictionary with:
        - result: Main lease details object containing all lease information
        - status_code: API response status ("ok" for success)

        INFORMATION AVAILABLE IN RESULT OBJECT:

        **Lease Term Information:**
        - lease_start: Lease start date in YYYY-MM-DD format (e.g., "2024-06-15")
        - lease_end: Lease end date in YYYY-MM-DD format (e.g., "2025-06-14")

        **Unit Information:**
        - unit: Unit identifier/number (e.g., "Apt 1203")

        **Occupant Information:**
        - occupants: Array of occupant names (e.g., ["John Doe", "Jane Doe"])

        **Parameters:**
        - pmc_id: Property Management Company ID (optional)
        - site_id: Site/Property ID (optional)
        - resident_household_id: Household identifier (optional)
        - resident_member_id: Individual resident member identifier (optional)

        This tool provides essential lease information that agents can use to answer questions about:
        - Lease duration and renewal dates
        - Current unit assignment
        - Who is listed on the lease
        - Lease term remaining

        Use this tool when residents ask about their lease details, move-out dates, lease renewal information, or occupant verification.
    """
)
async def get_lease_term_information(
    company_id: int | None = None,
    property_id: int | None = None,
    resident_household_id: int | None = None,
):
    """Return a simple lease details stub."""
    return {
        "result": {
            "lease_start": generate_date_iso(months=-1),
            "lease_end": generate_date_iso(months=11),
            "unit": "Apt 1203",
            "occupants": ["John Doe", "Jane Doe"],
            "buildingNumber": "125",
        },
        "status_code": "ok",
    }


@mcp.tool()
async def create_service_request(
    pmc_id: int,
    site_id: int,
    resident_household_id: int,
    resident_member_id: int,
    emergency: bool,
    chat_summary: str,
):
    """Create a maintenance service request for the resident household.

    Args:
        pmc_id: Property management company identifier.
        site_id: Site identifier for the property.
        resident_household_id: Household identifier for the resident.
        resident_member_id: Resident member identifier.
        emergency: Flag indicating whether this is an emergency request.
        chat_summary: Summary of the conversation in natural language.

    Returns:
        dict: Response payload
            - service_request_id: ID of the created service request
            - service_request_created: Boolean indicating whether the service request was created
            - priority_number: Priority code of the service request (e.g., "3")
            - priority_name: Priority name of the service request
            - agent_response: Agent response to the service request
    """
    # added a manual flag to switch modes
    IS_FAILURE = False

    # Return priority 1 (Emergency) if emergency=True, otherwise priority 3 (Standard)
    priority_number = "1" if emergency else "3"
    priority_name = "Emergency" if emergency else "Standard"

    if IS_FAILURE:
        return {
            "service_request_id": None,
            "service_request_created": False,
            "priority_number": None,
            "priority_name": None,
            "agent_response": "Service request failed to create.",
        }
    else:
        return {
            "service_request_id": 53362,
            "service_request_created": True,
            "priority_number": priority_number,
            "priority_name": priority_name,
            "agent_response": "Service request created successfully.",
        }


@mcp.tool()
async def get_active_service_requests(
    pmc_id: int,
    site_id: int,
    resident_household_id: int,
    resident_member_id: int,
):
    """
    Retrieve the list of active service requests for a specific household resident associated with the provided pmc_id,
    site_id, resident_household_id, and resident_member_id. The service requests typically represent maintenance or
    repair tasks that are currently in progress or awaiting completion.

    Parameters:
        pmc_id (int): The ID of the property management company (PMC) associated with the requested data.
        site_id (int): The ID of the site or property location where the service requests are being retrieved.
        resident_household_id (int): The unique identifier of the household to which the service requests belong.
        resident_member_id (int): The unique identifier of the household member who requested or is associated
                                   with the service requests.

    Returns:
        Dict: A dictionary containing a list of active service requests. Each service request includes details
        such as service_request_id, created_date, category, description, status, technician_notes, and requestor_name.
    """
    return {
        "result": [
            {
                "service_request_id": 43399,
                "created_date": generate_date_mmddyyyy(months=-3),
                "category": "Clothes dryer",
                "description": "Dryer does not work properly",
                "status": "Completed",
                "technician_notes": "The dryer is leaving the cloethes damp after a cycle.",
                "requestor_name": "Matt AB Dubois",
            },
            {
                "service_request_id": 43398,
                "created_date": generate_date_mmddyyyy(months=-5),
                "category": "Switch",
                "description": "Light switch does not work",
                "status": "In progress",
                "technician_notes": "The bedroom light switch is broken and not functioning.",
                "requestor_name": "Matt AB Dubois",
            },
            {
                "service_request_id": 43381,
                "created_date": generate_date_mmddyyyy(months=-6),
                "category": "Dishwasher",
                "description": "DW leaks",
                "status": "Completed",
                "technician_notes": "The dishwasher is leaking water, likely due to a faulty seal, hose, or connection.",
                "requestor_name": "Matt AB Dubois",
            },
            {
                "service_request_id": 43236,
                "created_date": generate_date_mmddyyyy(months=-7),
                "category": "Microwave",
                "description": "Mw fan does not work properly",
                "status": "Completed",
                "technician_notes": "",
                "requestor_name": "Matt Dubois",
            },
        ]
    }


@mcp.tool(
    description="""
    Returns self service steps for a given request type.
    Supported request types: "change light bulb", "change air filter", "reset circuit breaker",
    "unclog drain", "replace smoke detector battery", "reset gfci outlet", "fix running toilet"
    """
)
async def get_self_service_steps(request_type: str):
    """Returns self service steps"""
    request_type = request_type.lower().strip()

    if "light bulb" in request_type or "lightbulb" in request_type:
        return {
            "steps": [
                "Turn off the light switch and wait for bulb to cool down",
                "Carefully unscrew the old light bulb counterclockwise",
                "Check the wattage on the old bulb and match it with the new one",
                "Screw in the new light bulb clockwise until snug (don't overtighten)",
                "Turn the light switch back on to test",
            ],
            "instructions": "Standard light bulb replacement for ceiling fixtures, lamps, and wall sconces",
            "safety_notes": [
                "Ensure the light switch is OFF before starting",
                "Let hot bulbs cool for 10-15 minutes before handling",
                "Never exceed the fixture's maximum wattage rating",
                "Use a stable ladder or step stool for ceiling fixtures",
            ],
            "completion_time": "5-10 minutes",
        }
    elif "air filter" in request_type or "hvac filter" in request_type:
        return {
            "steps": [
                "Turn off your HVAC system at the thermostat",
                "Locate the air filter (usually near the air handler or return air vent)",
                "Note the direction of airflow arrows on the old filter",
                "Remove the old filter and dispose of it properly",
                "Insert the new filter with arrows pointing in the same direction",
                "Turn your HVAC system back on",
            ],
            "instructions": "Replace HVAC air filter to maintain air quality and system efficiency",
            "safety_notes": [
                "Turn off HVAC system before replacing filter",
                "Check filter size (usually printed on the side) before purchasing replacement",
                "Replace filters every 1-3 months depending on usage",
                "Ensure arrows point toward the air handler/furnace",
            ],
            "completion_time": "10-15 minutes",
        }
    elif "circuit breaker" in request_type or "breaker" in request_type:
        return {
            "steps": [
                "Locate your electrical panel/breaker box",
                "Look for a breaker switch that is in the middle position or 'OFF'",
                "Push the tripped breaker fully to the 'OFF' position first",
                "Then push it firmly to the 'ON' position",
                "Test the affected outlet or fixture",
            ],
            "instructions": "Reset a tripped circuit breaker to restore power",
            "safety_notes": [
                "Never touch the panel with wet hands",
                "If the breaker trips again immediately, contact maintenance",
                "Do not force a breaker that won't reset",
                "If you smell burning or see sparks, call maintenance immediately",
            ],
            "completion_time": "5 minutes",
        }
    elif "drain" in request_type and "clog" in request_type:
        return {
            "steps": [
                "Remove any visible debris from the drain opening",
                "Pour hot (not boiling) water down the drain",
                "Try using a plunger designed for sinks",
                "If still clogged, try a mixture of baking soda and vinegar",
                "Let it sit for 15 minutes, then flush with hot water",
            ],
            "instructions": "Clear minor drain clogs in sinks and tubs",
            "safety_notes": [
                "Never mix different drain cleaners",
                "Avoid using boiling water on PVC pipes",
                "If water backs up or drain remains clogged, contact maintenance",
                "Don't use chemical drain cleaners without approval",
            ],
            "completion_time": "20-30 minutes",
        }
    elif "smoke detector" in request_type or "smoke alarm" in request_type:
        return {
            "steps": [
                "Identify which smoke detector is beeping (usually chirps every 30-60 seconds)",
                "Locate the battery compartment (may require twisting or sliding the unit)",
                "Remove the old 9V battery",
                "Insert the new 9V battery, ensuring proper connection",
                "Test the detector by pressing the test button",
                "Replace the detector cover securely",
            ],
            "instructions": "Replace smoke detector battery when low battery chirping occurs",
            "safety_notes": [
                "Use a stable ladder or step stool",
                "Test the detector after battery replacement",
                "If chirping continues after battery replacement, contact maintenance",
                "Never remove batteries without replacing them immediately",
            ],
            "completion_time": "10 minutes",
        }
    elif "gfci" in request_type or "ground fault" in request_type or "outlet not working" in request_type:
        return {
            "steps": [
                "Locate the GFCI outlet that's not working (usually in bathroom, kitchen, or outdoor areas)",
                "Look for the 'TEST' and 'RESET' buttons on the outlet face",
                "Press the 'RESET' button firmly until it clicks and stays in",
                "Test the outlet by plugging in a small device like a phone charger",
                "If it doesn't work, check other GFCI outlets in the area - one may control multiple outlets",
            ],
            "instructions": "Reset a GFCI (Ground Fault Circuit Interrupter) outlet that has tripped",
            "safety_notes": [
                "GFCI outlets protect against electrical shock in wet areas",
                "Never use electrical devices with wet hands",
                "If the GFCI won't reset or trips immediately, contact maintenance",
                "Don't force the reset button if it won't stay in",
            ],
            "completion_time": "5 minutes",
        }
    elif "toilet" in request_type and ("running" in request_type or "won't stop" in request_type):
        return {
            "steps": [
                "Remove the toilet tank lid and set it aside safely",
                "Check if the chain connecting the flush handle to the flapper is too long or short",
                "Adjust the chain so there's slight slack when the flapper is closed",
                "Ensure the flapper sits flat against the valve seat",
                "If the float is stuck, gently move it to the correct position",
                "Replace the tank lid and test the flush",
            ],
            "instructions": "Fix a toilet that keeps running after flushing",
            "safety_notes": [
                "The water in the tank is clean and safe to touch",
                "Be careful not to drop the heavy tank lid",
                "If adjustments don't work, contact maintenance",
                "Don't force any components",
            ],
            "completion_time": "15 minutes",
        }
    else:
        return {
            "error": "Unknown self-service request type",
            "supported_types": [
                "change light bulb",
                "change air filter",
                "reset circuit breaker",
                "unclog drain",
                "replace smoke detector battery",
                "reset gfci outlet",
                "fix running toilet",
            ],
            "instructions": "For other maintenance issues, please submit a service request through the maintenance portal.",
        }


@mcp.tool(
    title="Send SMS on Behalf of Manager",
    name="send_sms_on_behalf_of_manager",
    description=(
        "Send an SMS message to a prospect or a resident on behalf of a manager. "
        "The message is sent using the specified manager's identity. "
        "Returns the SMS delivery status and details."
    ),
)
async def send_sms_on_behalf_of_manager(
    *,
    stream_id: Annotated[str, "The id of the conversation stream to send the message to."],
    send_as_manager_id: Annotated[int, "The identifier of the manager to send the message."],
    body: Annotated[str, "The text content of the SMS message."],
) -> Dict[str, Any]:
    """Sends an SMS message on behalf of the property manager

    Args:
        stream_id: The id of the conversation stream to send the message to
        send_as_manager_id: The identifier of the manager to send the message as
        body: The message content to send via SMS

    Returns:
        str: Confirmation message
    """
    return {"status": "success"}


@mcp.tool()
async def transfer_to_staff_voice(reason: str):
    """Transfers the call to a staff member"""
    return "Call was successfully transferred to a staff member."


# removed for send_sms_on_behalf_of_manager


@mcp.tool(
    name="fetch_community_events",
    title="Fetch Community Events",
    description="""
        Retrieve a comprehensive list of community events for residents with detailed event information.
        
        Call this prior to any workflow that requires a community event id.

        RETURN FORMAT: Returns an EventsResponse object containing:
        - events: List[CommunityEvent] - A list of event objects with complete event details
        
        EVENT INFORMATION INCLUDES:
        
        **Basic Event Information:**
        - id: Unique identifier for the event (e.g., "740046")
        - title: Event title/name
        - description: Detailed description of the event
        
        **Timing Details:**
        - startDate: Event start date and time in ISO 8601 format with timezone
        - endDate: Event end date and time in ISO 8601 format with timezone
        - isAllDayEvent: Boolean indicating if the event lasts all day
        
        **Event Status:**
        - isSignUpRequired: Boolean flag indicating event registration behavior — when true, residents must sign up before attending; when false, no sign-up is required and residents may attend freely.

        **Additional Information:**
        - imageUrl: URL to event image if available (null otherwise)
        - paymentType: Type of payment required (if any)
        - maxGuests: Maximum number of guests allowed (if applicable)
        - price: Event price (null if free)
        
        This tool provides a complete view of all community events, enabling residents to stay informed
        about upcoming activities and participate in community life.
        """,
)
async def fetch_community_events(resident_id: int | str | None = None, community_id: int | str | None = None):
    """Fetches community events"""
    mocked_response = {
        "events": [
            {
                "id": "383213",
                "description": "Casual rooftop gathering with light snacks, mocktails, and a meet-your-neighbors icebreaker to help new residents connect.",
                "title": "Sunset Social Mixer",
                "startDate": generate_datetime_string(7, 21, 0, "-07:00"),
                "endDate": generate_datetime_string(7, 23, 0, "-07:00"),
                "imageUrl": None,
                "isSignUpRequired": True,
                "isAllDayEvent": False,
                "hasUserSignedUp": False,
                "paymentType": None,
                "maxGuests": None,
                "price": "$100",
            },
            {
                "id": "384387",
                "description": "Informal afternoon gathering to discuss emerging tech over tea and snacks.",
                "title": "Tech & Tea Social",
                "startDate": generate_datetime_string(14, 14, 0, "-07:00"),
                "endDate": generate_datetime_string(14, 16, 0, "-07:00"),
                "imageUrl": None,
                "isSignUpRequired": True,
                "isAllDayEvent": False,
                "hasUserSignedUp": False,
                "paymentType": None,
                "maxGuests": 50,
                "price": None,
            },
            {
                "id": "352110",
                "description": "yes",
                "title": "Toga party",
                "startDate": generate_datetime_string(21, 0, 0, "-08:00"),
                "endDate": generate_datetime_string(21, 23, 59, "-08:00"),
                "imageUrl": None,
                "isSignUpRequired": False,
                "isAllDayEvent": True,
                "hasUserSignedUp": False,
                "paymentType": None,
                "maxGuests": None,
                "price": None,
            },
        ]
    }

    return mocked_response


@mcp.tool(
    name="sign_up_community_events",
    title="Sign up for Community Events",
    description=(
        "Register a resident for a community event. "
        "Prior to calling, make sure to call the necessary tools to fetch the correct event id. "
        "Returns the registration details if successful, or an error message if the event was not found."
    ),
    output_schema={
        "type": "object",
        "properties": {
            "registerEvent": {
                "type": ["object", "null"],
                "properties": {
                    "eventId": {"type": "string"},
                    "eventSignupId": {"type": "string"},
                    "guests": {"type": "integer"},
                    "successText": {"type": "string"},
                    "paymentText": {"type": "string"},
                    "attendeesCount": {"type": "integer"},
                    "totalCost": {"type": "string"},
                },
                "required": [
                    "eventId",
                    "eventSignupId",
                    "guests",
                    "successText",
                    "paymentText",
                    "attendeesCount",
                    "totalCost",
                ],
                "additionalProperties": False,
            }
        },
        "required": ["registerEvent"],
        "additionalProperties": False,
    },
)
async def sign_up_community_events(
    event_id: int | str | None = None,
    resident_id: int | str | None = None,
    guests: int | None = None,
):
    return {
        "registerEvent": {
            "eventId": "384387",
            "eventSignupId": "591304",
            "guests": 2,
            "successText": f"Sunset Rooftop Yoga Session {format_event_date(14, 14, 0, 16, 0)}",
            "paymentText": " You are now signed up!",
            "attendeesCount": 4,
            "totalCost": "0",
        }
    }


@mcp.tool(
    name="fetch_user_signed_up_community_events",
    title="Fetch User Signed Up Community Events",
    description="""
            Retrieve a comprehensive list of signed up community events for residents with detailed event information.
            
            Call this prior to any workflow that requires a user event registration id.

            RETURN FORMAT: Returns an EventsResponse object containing:
            - events: List[CommunityEvent] - A list of event objects with complete event details
            
            EVENT INFORMATION INCLUDES:
            
            **Basic Event Information:**
            - id: Unique identifier for the event (e.g., "740046")
            - title: Event title/name
            - description: Detailed description of the event
            
            **Timing Details:**
            - startDate: Event start date and time in ISO 8601 format with timezone
            - endDate: Event end date and time in ISO 8601 format with timezone
            - isAllDayEvent: Boolean indicating if the event lasts all day
            
            **Event Status:**
            - isSignUpAllowed: Boolean indicating if users can sign up
            - hasUserSignedUp: Boolean indicating if current user has signed up
            
            **Additional Information:**
            - imageUrl: URL to event image if available (null otherwise)
            - paymentType: Type of payment required (if any)
            - maxGuests: Maximum number of guests allowed (if applicable)
            - price: Event price (null if free)
            
            This tool provides a complete view of all community events, enabling residents to stay informed
            about upcoming activities and participate in community life.
            """,
)
async def fetch_user_signed_up_community_events(resident_id: int | str | None = None):
    return {
        "eventsReservations": [
            {
                "id": "591300",
                "event": {
                    "id": "383213",
                    "title": "Sunset Social Mixer",
                    "description": "Casual rooftop gathering with light snacks, mocktails, and a meet-your-neighbors icebreaker to help new residents connect.",
                    "startDate": generate_datetime_string(7, 21, 0, "-07:00"),
                    "endDate": generate_datetime_string(7, 23, 0, "-07:00"),
                },
            }
        ]
    }


@mcp.tool(
    name="cancel_community_event",
    title="Cancel Community Event",
    description="""
        This tools allows you to cancel a resident's signed-up event by providing the event reservation ID.
        
        Prior to calling, make sure to call the necessary tools to fetch the correct event id. 
        
        RETURN FORMAT: Returns an object containing:
        - cancelEventReservation: CancelCommunityEventResponse - An object with event reservation details
        
        EVENT INFORMATION INCLUDES:
        
        **Basic Event Information:**
        - id: Unique identifier for the event (e.g., "740046")
        - paymentStatus: Payment status of the event
        - createdDate: Date and time the event was created
        - guestsNumber: Number of guests attending the event
        
        """,
)
async def cancel_community_event(
    event_reservation_id: int | str,
    resident_id: int | str | None = None,
):
    return {
        "success": True,
        "message": "You have successfully cancelled the event.",
        "cancelEventReservation": {
            "id": "301718",
            "paymentStatus": "NOT_APPLICABLE",
            "createdDate": generate_datetime_string(-30, 18, 10, "-04:00"),
            "guestsNumber": 1,
        },
    }


@mcp.tool()
async def get_residents_packages(resident_id: str):
    return {
        "packages_list": [
            {
                "packageType": "Box",
                "packageStation": "Station A",
                "retrievedDate": None,
                "receivedBy": None,
                "comments": "Fragile - Handle with care",
                "trackingNumber": "123456789",
            },
            {
                "packageType": "Envelope",
                "packageStation": "Station B",
                "retrievedDate": None,
                "receivedBy": None,
                "comments": "Urgent delivery",
                "trackingNumber": "987654321",
            },
        ],
        "packages_count": 2,
    }


@mcp.tool(
    name="check_resident_sms_opt_in_status",
    title="Check SMS Opt In Status",
    description=(
        "Check if a resident has opted in to SMS communication."
        "Acceptable input fields and their types:\n"
        "- resident_id: int\n"
        "Returns the resident's SMS information if successful."
    ),
)
async def check_resident_sms_opt_in_status(
    resident_id: Annotated[
        int,
        Field(description=("The request object. Fields:\n- resident_id: int\n")),
    ],
):
    """
    Check a resident's SMS opt in status.
    """

    if resident_id == NON_CONSENTING_RESIDENT_ID:
        return {"sms_consent": {"status": "revoked"}}
    elif resident_id == NEW_RESIDENT_ID:
        return {"sms_consent": {"status": "new"}}
    return {"sms_consent": {"status": "granted"}}


@mcp.tool(
    name="update_resident_sms_consent_information",
    title="Update Resident SMS Consent",
    description=(
        "Update a resident's SMS consent. "
        "Acceptable input fields and their types:\n"
        "- sms_consent: boolean (True if the resident consents to SMS communication)\n"
        "Returns the updated resident data if successful."
    ),
)
async def update_resident_sms_consent_information(
    request: Annotated[
        ResidentUpdateRequest,
        Field(
            description=(
                "The update request object. Fields:\n- resident_id: int\n- sms_consent: boolean defaults to false\n"
            )
        ),
    ],
) -> dict[any, any] | None:
    """
    Update a resident's SMS consent.
    """
    return {"sms_consent": "granted"}


@mcp.tool()
async def issue_guest_parking_pass(
    resident_id: str | None = None,
    vehicle_make: str | None = None,
    vehicle_model: str | None = None,
    vehicle_license_plate: str | None = None,
):
    """Issues a guest parking pass for a resident"""
    return {
        "data": {
            "addParkingPass": {
                "id": "2047431",
                "downloadUrl": "https://cassidysouth.qa2.loftliving.com/portal/parking-passes/print/passId/2047431",
                "dateInserted": generate_datetime_string(82, 23, 17, "-07:00"),
                "validFrom": generate_datetime_string(82, 5, 0, "-07:00"),
                "validTo": generate_datetime_string(83, 17, 0, "-07:00"),
                "vehicleMake": "RAM",
                "vehicleModel": "TRX",
                "vehicleLicensePlate": "TX-6666",
            }
        }
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8042, path="/")
