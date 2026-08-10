from ai_cat_controller.domain.growth import GrowthEventType
from ai_cat_controller.services.growth_engine import GrowthEngine


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


def test_personality_bias_changes_growth_without_changing_event_mapping() -> None:
    engine = GrowthEngine()
    common = {
        "event_type": GrowthEventType.KNOWLEDGE_DISCUSSION,
        "engagement": 1.0,
        "topic": "robotics",
        "current_attributes": _attributes(),
        "recent_events": [],
    }

    scholar = engine.calculate(personality_id="curious_scholar", **common)
    gentle = engine.calculate(personality_id="gentle_companion", **common)

    assert scholar["attribute_delta"]["curiosity"] > gentle["attribute_delta"]["curiosity"]
    assert scholar["attribute_delta"]["knowledge"] > gentle["attribute_delta"]["knowledge"]


def test_repeated_event_and_topic_decay_growth() -> None:
    engine = GrowthEngine()
    first = engine.calculate(
        event_type=GrowthEventType.QUESTION,
        personality_id="sunny_explorer",
        engagement=1.0,
        topic="robotics",
        current_attributes=_attributes(),
        recent_events=[],
    )
    repeated = engine.calculate(
        event_type=GrowthEventType.QUESTION,
        personality_id="sunny_explorer",
        engagement=1.0,
        topic="robotics",
        current_attributes=_attributes(),
        recent_events=[
            {"event_type": "question", "topic": "robotics"}
            for _ in range(6)
        ],
    )

    assert first["repetition_decay"] == 1.0
    assert first["novelty"] == 1.0
    assert repeated["repetition_decay"] == 0.4
    assert repeated["novelty"] == 0.4
    assert repeated["attribute_delta"]["curiosity"] < first["attribute_delta"]["curiosity"]


def test_growth_delta_never_exceeds_attribute_cap() -> None:
    result = GrowthEngine().calculate(
        event_type=GrowthEventType.KNOWLEDGE_DISCUSSION,
        personality_id="curious_scholar",
        engagement=1.0,
        topic="science",
        current_attributes=_attributes(curiosity=9990, knowledge=9995),
        recent_events=[],
    )

    assert result["attribute_delta"]["curiosity"] == 10
    assert result["attribute_delta"]["knowledge"] == 5
