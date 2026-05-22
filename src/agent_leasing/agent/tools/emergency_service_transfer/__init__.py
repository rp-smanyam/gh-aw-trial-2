from agents import FunctionTool

from agent_leasing.agent.util import SessionScope
from agent_leasing.api.model import EmergencyServiceProduct

from .advanced.emergency_service_transfer_advanced import (
    emergency_service_transfer_advanced,
    get_emergency_service_transfer_advanced_fxn,
)
from .basic.emergency_service_transfer_basic import (
    emergency_service_transfer_basic,
    get_emergency_service_transfer_basic_fxn,
)
from .rpcc.emergency_service_transfer_rpcc import (
    emergency_service_transfer_rpcc,
    get_emergency_service_transfer_rpcc_fxn,
)


def get_emergency_service_transfer_fxn(
    emergency_service_product: EmergencyServiceProduct, context: SessionScope | None = None
) -> FunctionTool:
    if emergency_service_product == EmergencyServiceProduct.BASIC:
        return get_emergency_service_transfer_basic_fxn(context)
    elif emergency_service_product == EmergencyServiceProduct.ADVANCED:
        return get_emergency_service_transfer_advanced_fxn(context)
    elif emergency_service_product == EmergencyServiceProduct.RPCC:
        return get_emergency_service_transfer_rpcc_fxn(context)
    else:
        raise ValueError(f"Unsupported emergency service product '{emergency_service_product}'")


__all__ = [
    "get_emergency_service_transfer_fxn",
    "emergency_service_transfer_basic",
    "emergency_service_transfer_advanced",
    "emergency_service_transfer_rpcc",
]
