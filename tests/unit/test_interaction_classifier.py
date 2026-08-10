from ai_cat_controller.domain.growth import GrowthEmotion, GrowthEventType
from ai_cat_controller.services.interaction_classifier import InteractionClassifier


def test_classifier_uses_fixed_dialog_enum_and_safe_topic() -> None:
    result = InteractionClassifier().classify("机器人电机为什么会转？")

    assert result.event_type == GrowthEventType.KNOWLEDGE_DISCUSSION
    assert result.topic == "robotics"
    assert 0.0 <= result.engagement <= 1.0


def test_invalid_classifier_json_falls_back_without_raising() -> None:
    result = InteractionClassifier().parse_json(
        '{"event_type":"invented","topic":"x","emotion":"unknown"}'
    )

    assert result.event_type == GrowthEventType.CASUAL_CHAT
    assert result.emotion == GrowthEmotion.NEUTRAL
