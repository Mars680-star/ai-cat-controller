"""Configuration-driven growth tag evaluation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ai_cat_controller.domain.growth import TAG_RULES, TagRule


class TagEngine:
    def evaluate(
        self,
        *,
        personality_id: str,
        intimacy_level: int,
        attributes: Mapping[str, int],
        event_counts: Mapping[str, int],
        earned_tag_ids: set[str],
    ) -> list[TagRule]:
        awarded: list[TagRule] = []
        for rule in TAG_RULES:
            if rule.tag_id in earned_tag_ids:
                continue
            if rule.required_personality_ids and (
                personality_id not in rule.required_personality_ids
            ):
                continue
            if (
                rule.required_intimacy_level is not None
                and intimacy_level < rule.required_intimacy_level
            ):
                continue
            if any(
                int(attributes.get(attribute.value, 0)) < minimum
                for attribute, minimum in rule.required_attributes.items()
            ):
                continue
            if any(
                sum(
                    int(event_counts.get(event_type.value, 0))
                    for event_type in requirement.event_types
                )
                < requirement.minimum
                for requirement in rule.required_event_counts
            ):
                continue
            awarded.append(rule)
            earned_tag_ids.add(rule.tag_id)
        return awarded

    @staticmethod
    def active_tag_ids(earned_tags: Sequence[Mapping[str, object]]) -> list[str]:
        return [
            str(tag["tag_id"])
            for tag in earned_tags
            if str(tag.get("status")) == "active"
        ]
