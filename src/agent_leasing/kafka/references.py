"""Builders for the `references` array on a TaskActivityEvent."""

REF_SOURCE_KNCK = "KNCK"
REF_TYPE_COMPANY = "COMPANY"
REF_TYPE_PROPERTY = "PROPERTY"
REF_TYPE_RESIDENT = "RESIDENT"


def build_activity_references(
    *,
    knock_company_id: str | None = None,
    knock_property_id: str | None = None,
    knock_resident_id: str | None = None,
) -> list[dict]:
    """Return the `references` list for a TaskActivityEvent.

    Each ref is a dict matching the Avro `Reference` record shape
    (`type`, `source`, `id`). None values are skipped cleanly.
    """
    refs: list[dict] = []
    if knock_company_id:
        refs.append({"type": REF_TYPE_COMPANY, "source": REF_SOURCE_KNCK, "id": str(knock_company_id)})
    if knock_property_id:
        refs.append({"type": REF_TYPE_PROPERTY, "source": REF_SOURCE_KNCK, "id": str(knock_property_id)})
    if knock_resident_id:
        refs.append({"type": REF_TYPE_RESIDENT, "source": REF_SOURCE_KNCK, "id": str(knock_resident_id)})
    return refs
