from ai_cat_controller.services.tag_engine import TagEngine


def test_attribute_threshold_without_event_evidence_does_not_award_tag() -> None:
    awarded = TagEngine().evaluate(
        personality_id="curious_scholar",
        intimacy_level=4,
        attributes={"curiosity": 8000, "knowledge": 8000},
        event_counts={},
        earned_tag_ids=set(),
    )

    assert awarded == []


def test_event_evidence_and_attributes_award_configured_tags_once() -> None:
    earned: set[str] = set()
    engine = TagEngine()
    common = {
        "personality_id": "curious_scholar",
        "intimacy_level": 1,
        "attributes": {"curiosity": 5600, "knowledge": 5600},
        "event_counts": {"question": 25, "knowledge_discussion": 25},
        "earned_tag_ids": earned,
    }

    first = engine.evaluate(**common)
    second = engine.evaluate(**common)

    assert {rule.tag_id for rule in first} == {"little_explorer", "little_scholar"}
    assert second == []


def test_advanced_tags_replace_lower_tags_by_configuration() -> None:
    rules = TagEngine().evaluate(
        personality_id="curious_scholar",
        intimacy_level=4,
        attributes={"curiosity": 8000, "knowledge": 8000},
        event_counts={"question": 30, "knowledge_discussion": 65},
        earned_tag_ids=set(),
    )

    by_id = {rule.tag_id: rule for rule in rules}
    assert by_id["explorer"].replaces == ("little_explorer",)
    assert by_id["scholar_cat"].replaces == ("little_scholar",)


def test_soft_hearted_requires_base_personality_and_intimacy() -> None:
    common = {
        "attributes": {"empathy": 7000},
        "event_counts": {"emotional_sharing": 25},
        "earned_tag_ids": set(),
    }

    wrong_personality = TagEngine().evaluate(
        personality_id="gentle_companion",
        intimacy_level=4,
        **common,
    )
    low_intimacy = TagEngine().evaluate(
        personality_id="proud_star",
        intimacy_level=1,
        **common,
    )
    matching = TagEngine().evaluate(
        personality_id="proud_star",
        intimacy_level=2,
        **common,
    )

    assert "soft_hearted" not in {rule.tag_id for rule in wrong_personality}
    assert "soft_hearted" not in {rule.tag_id for rule in low_intimacy}
    assert "soft_hearted" in {rule.tag_id for rule in matching}
