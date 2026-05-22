OPENAPI_EXAMPLES = {
    "resident_one_chat": {
        "summary": "Say hello to the agent",
        "description": "Hello message",
        "value": {
            "product": "resident_one_chat",
            "prompt": "hello",
            "chat_session_id": "1",
            "request_id": "2",
            "product_info": {
                "knock_prospect_id": "95946",
                "knock_property_id": "21521",
                "ai_config": {
                    "is_sms_enabled": False,
                    "is_gen_ai_sms_enabled": False,
                    "resident_virtual_agent_sms": False,
                    "resident_virtual_agent_sms_gen_ai": False,
                    "chat_rollover": None,
                    "schedule_tour_va_enabled": False,
                    "pna_va_enabled": False,
                    "rpcc_agent_rollover": False,
                },
                "uc_first_name": "Resident",
                "uc_last_name": "Tester",
                "uc_company_id": {"id": 9999999, "source": "OS"},
                "uc_property_id": {"id": 8888888, "source": "OS"},
                "uc_guestcard_id": {"id": 777, "source": "OS"},
                "uc_customer_id": {"id": 666, "source": "OS"},
            },
        },
    },
    "renter_ai_prospect": {
        "summary": "Say hello to the prospect agent",
        "description": "A simple hello",
        "value": {
            "product": "renter_ai_prospect",
            "prompt": "hello",
            "chat_session_id": "1",
            "request_id": "2",
            "product_info": {
                "knock_prospect_id": "95946",
                "knock_property_id": "21521",
            },
        },
    },
    "simple": {
        "summary": "Say hello to the simple agent",
        "description": "A simple hello",
        "value": {
            "product": "simple",
            "prompt": "hello",
            "chat_session_id": "1",
            "request_id": "2",
            "product_info": {
                "knock_prospect_id": "95946",
                "knock_property_id": "21521",
            },
        },
    },
    "renter_ai_prospect_advanced": {
        "summary": "Say hello to the prospect agent with more context",
        "description": "Include more context in the response",
        "value": {
            "product": "renter_ai_prospect",
            "prompt": "hello",
            "confirmation": "",
            "chat_session_id": "1",
            "flow_id": "2",
            "state": "",
            "request_type": "standard",
            "product_info": {
                "knock_prospect_id": "95946",
                "knock_property_id": "21521",
                "ai_config": {
                    "is_sms_enabled": True,
                    "is_gen_ai_sms_enabled": True,
                    "resident_virtual_agent_sms": True,
                    "resident_virtual_agent_sms_gen_ai": True,
                    "chat_rollover": None,
                    "schedule_tour_va_enabled": True,
                    "pna_va_enabled": True,
                    "rpcc_agent_rollover": False,
                },
                "property_name": "Cassidy South",
                "property_preferences": {
                    "ai_cross_sell_availability_url": "https://www.google.com",
                    "in_person_tours": True,
                    "live_video_tour_type": True,
                    "self_guided_tour_button_label": "self",
                    "self_guided_tour_url": "https://example.com",
                    "self_guided_tours_enabled": True,
                    "tours_export_only_favorite_unit": False,
                    "virtual_tour_links": False,
                    "virtual_tour_links_mapping": [],
                },
                "property_address": {
                    "city": "McKinney",
                    "house": "",
                    "neighborhood": "Canyon Creek",
                    "raw": "1401 n custer rd McKinney TX 75072",
                    "state": "TX",
                    "street": "1401 n custer rd",
                    "zip": "75072",
                },
                "property_timezone": "America/Chicago",
                "uc_first_name": "hello openai",
                "uc_last_name": "world",
                "uc_company_id": {"id": 7595477, "source": "OS"},
                "uc_property_id": {"id": 7595492, "source": "OS"},
                "uc_guestcard_id": {"id": 66, "source": "OS"},
                "uc_customer_id": {"id": 60, "source": "OS"},
                "uc_resident_member_id": {"id": 67, "source": "OS"},
                "uc_resident_household_id": {"id": 68, "source": "OS"},
                "ab_resident_id": {"id": "1234", "source": "AB"},
                "ab_resident_uuid": {
                    "id": "417d5f28-9d88-4487-b2d3-3bbcbb9c9a4f",
                    "source": "AB",
                },
            },
        },
    },
}

AGENT_ASK_DESCRIPTION = """
Speak with an agent.

- Specify `resident_one_chat` as a product to interact with the resident agent.

Variations: `*_sms` or `*_email`.
"""

AGENT_STREAM_DESCRIPTION = """
Speak with an agent using server side events (SSE).

- Specify `resident_one_chat` as a product to interact with the resident agent.

Variations: `*_sms` or `*_email`.

The output will appear similar to:
```
Content-Type: text/event-stream
Cache-Control: no-cache
Connection: keep-alive

data: {"content": "", "phase": "thinking", "status": "active", "elapsed": 50}
data: {"content": "", "phase": "searching", "status": "active", "elapsed": 2500}
data: {"content": "Based ", "phase": "generating", "status": "active", "elapsed": 3200}
data: {"content": "on the search results...", "phase": "generating", "status": "done", "done": true}
data: [DONE]
```
"""

OPENAPI_STREAMING_EXAMPLES = {
    "resident_streaming_minimal": {
        "summary": "Minimal streaming payload",
        "value": {
            "product": "renter_one_chat",
            "message": {"content": "text"},
            "prompt": "not used",
            "property_id": "21521",
            "chat_session_id": "1",
            "flow_id": "2",
            "thread_id": "string",
            "product_info": {
                "knock_resident_id": "95946",
                "knock_property_id": "21521",
                "property_name": "Cassidy South",
                "property_timezone": "America/Chicago",
                "uc_first_name": "hello openai",
                "uc_last_name": "world",
                "uc_company_id": {"id": 7595477, "source": "OS"},
                "uc_community_id": {"id": "6838", "source": "AB"},
                "uc_community_uuid": {
                    "id": "ddbcd417-7485-4ede-abe4-c4ddbcabc330",
                    "source": "AB",
                },
                "uc_consumer_identity_token": {"id": "string", "source": "CIDP"},
                "uc_property_id": {"id": 7595492, "source": "OS"},
                "resident_manager_id": None,
                "uc_guestcard_id": {"id": 66, "source": "OS"},
                "uc_customer_id": {"id": 60, "source": "OS"},
                "uc_resident_member_id": {"id": 67, "source": "OS"},
                "uc_resident_household_id": {"id": 68, "source": "OS"},
                "ab_resident_id": {"id": "1234", "source": "AB"},
                "ab_resident_uuid": {
                    "id": "417d5f28-9d88-4487-b2d3-3bbcbb9c9a4f",
                    "source": "AB",
                },
            },
        },
    },
    "resident_streaming": {
        "summary": "Streaming payload",
        "value": {
            "product": "renter_one_chat",
            "message": {"content": "text"},
            "prompt": "not used",
            "property_id": "21521",
            "chat_session_id": "1",
            "flow_id": "2",
            "thread_id": "string",
            "product_info": {
                "knock_resident_id": "95946",
                "knock_property_id": "21521",
                "property_name": "Cassidy South",
                "property_timezone": "America/Chicago",
                "uc_first_name": "hello openai",
                "uc_last_name": "world",
                "uc_company_id": {"id": 7595477, "source": "OS"},
                "uc_community_id": {"id": "6838", "source": "AB"},
                "uc_community_uuid": {
                    "id": "ddbcd417-7485-4ede-abe4-c4ddbcabc330",
                    "source": "AB",
                },
                "uc_consumer_identity_token": {"id": "string", "source": "CIDP"},
                "uc_property_id": {"id": 7595492, "source": "OS"},
                "resident_manager_id": None,
                "uc_guestcard_id": {"id": 66, "source": "OS"},
                "uc_customer_id": {"id": 60, "source": "OS"},
                "uc_resident_member_id": {"id": 67, "source": "OS"},
                "uc_resident_household_id": {"id": 68, "source": "OS"},
                "ab_resident_id": {"id": "1234", "source": "AB"},
                "ab_resident_uuid": {
                    "id": "417d5f28-9d88-4487-b2d3-3bbcbb9c9a4f",
                    "source": "AB",
                },
            },
            "static_paths": {
                "payment_and_ledger": "/portal/payments",
                "amenities": "/portal/reservations",
                "reservations": None,
                "parking": "/portal/parking-passes",
                "package": "/portal/packages",
                "community_events": "/portal/events",
                "human_hand_off": "/portal/messenger",
                "service_request": "/portal/mr",
                "front_desk_instructions": "/portal/fdi",
                "resident_checklist": "/portal/resident-checklist",
                "parking_passes": "/portal/parking-passes",
                "community_wall": "/portal/wall",
                "single_service_request": "/portal/mr/detail/mrId",
                "all_open_service_request": "/portal/mr/index/status/open",
            },
        },
    },
}
