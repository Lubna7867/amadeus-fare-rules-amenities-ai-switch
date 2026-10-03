import pytest

from farelens.schemas.amenities import AmenitiesRequest, AmenityItem, FareNamesRequest
from farelens.services.amenities import AmenitiesService, fallback_fare_label
from farelens.services.amenities_text import (
    build_amenity_cache_key,
    extract_first_url,
    extract_json,
    normalize_amenity_for_prompt,
    strip_urls,
)
from farelens.services.prompts import PROMPTS, validate_prompt


def test_normalisation_and_cache_key():
    a = build_amenity_cache_key("1pc x 7kg", namespace="v1")
    b = build_amenity_cache_key("<b>1PC</b> X 7KG ", namespace="v1")
    assert a == b and len(a) == 64
    assert a != build_amenity_cache_key("1pc x 7kg", namespace="v2")
    assert normalize_amenity_for_prompt("Lounge&nbsp;access<br>Not included") == "Lounge access Not included"


def test_url_helpers():
    text = "Seat selection for a fee. See https://airline.example/seats."
    assert extract_first_url(text) == "https://airline.example/seats"
    assert strip_urls(text) == "Seat selection for a fee. See"


def test_extract_json_tolerates_prose_and_fences():
    assert extract_json('Here you go:\n```json\n{"amenities": []}\n```') == {"amenities": []}
    assert extract_json('{"ECOLITE": "Economy Lite"} thanks') == {"ECOLITE": "Economy Lite"}
    assert extract_json("[1, 2]") == [1, 2]
    with pytest.raises(ValueError):
        extract_json("no json here")


def test_schemas_normalise_input():
    req = AmenitiesRequest.model_validate({"amenities": [" 1pc x 7kg ", "", "Lounge"], "lang": "AR"})
    assert req.amenities == ["1pc x 7kg", "Lounge"] and req.lang == "ar" and req.summarize is True
    names = FareNamesRequest.model_validate({"codes": ["ecolite", "ECOLITE", " busiflex "]})
    assert names.names == ["ECOLITE", "BUSIFLEX"]
    item = AmenityItem(type="cabinbaggage", description=" 1 cabin bag ")
    assert item.type == "CabinBaggage" and item.description == "1 cabin bag"
    assert AmenityItem(type="nonsense", description="x").type == "Other"


def test_collapse_merges_identical_items():
    items = [
        {"type": "Lounge", "description": "Lounge access not included", "details": "", "is_chargeable": False, "ref_url": ""},
        {"type": "Lounge", "description": "lounge access not included", "details": "Except at DXB", "is_chargeable": False, "ref_url": "https://x"},
        {"type": "Meal", "description": "Meal included", "details": "", "is_chargeable": False, "ref_url": ""},
    ]
    out = AmenitiesService._collapse(items)
    assert len(out) == 2
    assert out[0]["details"] == "Except at DXB" and out[0]["ref_url"] == "https://x"


def test_fallback_fare_label():
    assert fallback_fare_label("ECOLITE") == "Economy Lite"
    assert fallback_fare_label("BUSIFLEX") == "Business Flex"
    assert fallback_fare_label("PREMSAVR") == "Premium Economy Saver"
    assert fallback_fare_label("ECOXQ7") == "Economy"
    assert fallback_fare_label("ZZZ") == "Standard"


def test_amenity_prompts_registered_and_valid(tmp_path):
    from farelens.core.config import PROJECT_ROOT
    from farelens.services.prompts import PromptService

    ps = PromptService(PROJECT_ROOT / "prompts")
    for pid in ("amenities_summary_system", "amenities_summary_user", "amenities_translate_system", "amenities_translate_user", "fare_name_system", "fare_name_user"):
        spec = PROMPTS[pid]
        assert validate_prompt(spec, ps.load_default(spec.file)) == []
