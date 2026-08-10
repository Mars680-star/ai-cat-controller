from ai_cat_controller.domain.personalities import PERSONALITY_BY_ID
from ai_cat_controller.services.behavior_profile_service import BehaviorProfileService


def _attributes(**overrides: int) -> dict[str, int]:
    values = {
        "curiosity": 0,
        "empathy": 0,
        "knowledge": 0,
        "energy": 0,
        "mischief": 0,
        "discipline": 0,
    }
    values.update(overrides)
    return values


def test_behavior_profile_preserves_base_personality_and_intimacy() -> None:
    profile = BehaviorProfileService().build(
        personality=PERSONALITY_BY_ID["proud_star"],
        intimacy_level=3,
        attributes=_attributes(empathy=6500),
        active_tag_ids=["caring_partner"],
    )

    assert profile["base_personality_id"] == "proud_star"
    assert profile["intimacy_level"] == 3
    assert profile["active_tag_ids"] == ["caring_partner"]
    assert any("嘴硬心软" in directive for directive in profile["directives"])


def test_same_growth_tag_has_distinct_base_personality_expression() -> None:
    service = BehaviorProfileService()
    gentle = service.build(
        personality=PERSONALITY_BY_ID["gentle_companion"],
        intimacy_level=2,
        attributes=_attributes(empathy=7000),
        active_tag_ids=["caring_partner"],
    )
    proud = service.build(
        personality=PERSONALITY_BY_ID["proud_star"],
        intimacy_level=2,
        attributes=_attributes(empathy=7000),
        active_tag_ids=["caring_partner"],
    )

    assert gentle["directives"] != proud["directives"]
    assert gentle["base_personality_id"] == "gentle_companion"
    assert proud["base_personality_id"] == "proud_star"


def test_small_attribute_changes_inside_band_do_not_change_revision() -> None:
    service = BehaviorProfileService()
    personality = PERSONALITY_BY_ID["sunny_explorer"]
    first = service.build(
        personality=personality,
        intimacy_level=1,
        attributes=_attributes(curiosity=4100),
        active_tag_ids=[],
    )
    second = service.build(
        personality=personality,
        intimacy_level=1,
        attributes=_attributes(curiosity=4199),
        active_tag_ids=[],
    )

    assert first["revision"] == second["revision"]


def test_same_base_and_intimacy_form_distinct_profiles_after_different_growth() -> None:
    service = BehaviorProfileService()
    personality = PERSONALITY_BY_ID["proud_star"]
    explorer = service.build(
        personality=personality,
        intimacy_level=3,
        attributes=_attributes(curiosity=8000, knowledge=8000),
        active_tag_ids=["explorer", "scholar_cat"],
    )
    companion = service.build(
        personality=personality,
        intimacy_level=3,
        attributes=_attributes(empathy=7000),
        active_tag_ids=["caring_partner", "soft_hearted"],
    )

    assert explorer["base_personality_id"] == companion["base_personality_id"]
    assert explorer["intimacy_level"] == companion["intimacy_level"]
    assert explorer["revision"] != companion["revision"]
    assert explorer["directives"] != companion["directives"]
    assert any("傲娇" in item for item in explorer["directives"])
    assert any("傲娇" in item for item in companion["directives"])
