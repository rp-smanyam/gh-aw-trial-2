import structlog

from agent_leasing.clients.ldp import call_ldp_api
from agent_leasing.settings import settings

logger = structlog.getLogger()


async def get_ldp_data(property_id: str) -> dict:
    if not settings.ldp_rp_api_url:
        return {}

    url = settings.ldp_rp_api_url + "/renter-read"
    data = {
        "dataset_id": "lz_renter_data_hub",
        "table_name": "property_info",
        "filters": {"and": [{"field": "property_id", "operator": "=", "value": f"{property_id}"}]},
        "offset": 0,
    }

    logger.info(f"LDP POST to url: {url} with data: {data}")

    return await call_ldp_api(url, data=data)
