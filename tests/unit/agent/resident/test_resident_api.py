import pytest
from starlette.status import HTTP_200_OK, HTTP_422_UNPROCESSABLE_ENTITY

pytest.skip(
    allow_module_level=True,
    reason="Resident is not merged to repo yet and model is not ready.",
)


async def test_post_resident_request_200(client):
    """Valid resident request should return 200."""

    ask_request = {
        "chat_session_id": "d2e35c21ad3b4d75ace24f19d68c6c35",
        "product": "resident",
        "product_info": {
            "knock_property_id": "21521",
            "knock_prospect_id": "1",
            "uc_property_id": {"id": 1, "source": ""},
            "uc_company_id": {"id": 1, "source": ""},
            "uc_resident_household_id": {"id": 1, "source": ""},
            "uc_resident_member_id": {"id": 1, "source": ""},
        },
        "prompt": "hello",
    }

    response = client.post("/v1/agent/ask", json=ask_request)
    assert response.status_code == HTTP_200_OK


async def test_post_resident_request_422(client):
    """Invalid resident request should return 422."""

    ask_request = {
        "chat_session_id": "d2e35c21ad3b4d75ace24f19d68c6c35",
        "product": "resident",
        "product_info": {
            "knock_property_id": "21521",
            "knock_prospect_id": "1",
            "uc_property_id": {"id": 1, "source": ""},
            "uc_resident_household_id": {"id": 1, "source": ""},
            "uc_resident_member_id": {"id": 1, "source": ""},
        },
        "prompt": "hello",
    }

    response = client.post("/v1/agent/ask", json=ask_request)
    assert response.status_code == HTTP_422_UNPROCESSABLE_ENTITY
