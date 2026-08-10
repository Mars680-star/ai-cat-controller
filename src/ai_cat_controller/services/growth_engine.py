"""Pure, explainable attribute delta calculation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ai_cat_controller.domain.growth import (
    ATTRIBUTE_MAX_UNITS,
    EVENT_BASE_GROWTH,
    PERSONALITY_GROWTH_BIAS,
    REPETITION_DECAY,
    TOPIC_NOVELTY_DECAY,
    TOUCH_GROWTH_OVERRIDES,
    GrowthAttribute,
    GrowthEventType,
)


class GrowthEngine:
    def calculate(
        self,
        *,
        event_type: GrowthEventType,
        personality_id: str,
        engagement: float,
        topic: str,
        current_attributes: Mapping[str, int],
        recent_events: Sequence[Mapping[str, object]],
        metadata: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        base = self._base_growth(event_type, metadata or {})
        repetition = self._repetition_factor(event_type, recent_events)
        novelty = self._novelty_factor(topic, recent_events)
        engagement = min(max(float(engagement), 0.0), 1.0)
        biases = PERSONALITY_GROWTH_BIAS[personality_id]
        delta: dict[str, int] = {}
        for attribute, base_units in base.items():
            available = ATTRIBUTE_MAX_UNITS - int(
                current_attributes.get(attribute.value, 0)
            )
            units = round(
                base_units
                * biases[attribute]
                * engagement
                * repetition
                * novelty
            )
            delta[attribute.value] = max(0, min(units, available))
        return {
            "attribute_delta": delta,
            "engagement": engagement,
            "novelty": novelty,
            "repetition_decay": repetition,
        }

    @staticmethod
    def _base_growth(
        event_type: GrowthEventType,
        metadata: Mapping[str, object],
    ) -> dict[GrowthAttribute, int]:
        if event_type == GrowthEventType.PHYSICAL_TOUCH:
            sensor = str(metadata.get("sensor", ""))
            return TOUCH_GROWTH_OVERRIDES.get(
                sensor,
                EVENT_BASE_GROWTH[event_type],
            )
        return EVENT_BASE_GROWTH[event_type]

    @staticmethod
    def _repetition_factor(
        event_type: GrowthEventType,
        recent_events: Sequence[Mapping[str, object]],
    ) -> float:
        consecutive = 0
        for event in recent_events:
            if str(event.get("event_type")) != event_type.value:
                break
            consecutive += 1
        return REPETITION_DECAY[min(consecutive, len(REPETITION_DECAY) - 1)]

    @staticmethod
    def _novelty_factor(
        topic: str,
        recent_events: Sequence[Mapping[str, object]],
    ) -> float:
        normalized = topic.strip().lower() or "general"
        repeated = sum(
            1
            for event in recent_events
            if str(event.get("topic", "")).strip().lower() == normalized
        )
        return TOPIC_NOVELTY_DECAY[
            min(repeated, len(TOPIC_NOVELTY_DECAY) - 1)
        ]
