from agent_leasing.kafka.references import build_activity_references


def test_empty_when_no_fields_present():
    assert build_activity_references() == []


def test_includes_company_property_resident_when_all_provided():
    refs = build_activity_references(knock_company_id="c-1", knock_property_id="p-2", knock_resident_id="r-3")
    assert refs == [
        {"type": "COMPANY", "source": "KNCK", "id": "c-1"},
        {"type": "PROPERTY", "source": "KNCK", "id": "p-2"},
        {"type": "RESIDENT", "source": "KNCK", "id": "r-3"},
    ]


def test_resident_omitted_when_missing():
    refs = build_activity_references(knock_company_id="c", knock_property_id="p")
    assert {"type": "RESIDENT", "source": "KNCK", "id": None} not in refs
    assert len(refs) == 2
    assert all(r["type"] != "RESIDENT" for r in refs)


def test_integer_ids_are_stringified():
    refs = build_activity_references(knock_company_id=123, knock_property_id=456, knock_resident_id=789)
    assert {r["type"]: r["id"] for r in refs} == {
        "COMPANY": "123",
        "PROPERTY": "456",
        "RESIDENT": "789",
    }


def test_source_is_always_knck_for_seed_refs():
    refs = build_activity_references(knock_company_id="c", knock_property_id="p", knock_resident_id="r")
    assert {r["source"] for r in refs} == {"KNCK"}
