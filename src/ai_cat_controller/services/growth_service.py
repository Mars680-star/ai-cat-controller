"""Persistence orchestration for non-blocking long-term personality growth."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from ai_cat_controller.domain.growth import (
    ATTRIBUTE_SCALE,
    GROWTH_ATTRIBUTE_LABELS,
    GROWTH_EVENT_LABELS,
    TAG_RULE_BY_ID,
    GrowthAttribute,
    GrowthClassification,
    GrowthEmotion,
    GrowthEventType,
    GrowthSourceType,
)
from ai_cat_controller.domain.intimacy import level_for_points
from ai_cat_controller.domain.personalities import PERSONALITY_BY_ID
from ai_cat_controller.persistence.sqlite_repository import SQLiteRepository
from ai_cat_controller.services.behavior_profile_service import BehaviorProfileService
from ai_cat_controller.services.growth_engine import GrowthEngine
from ai_cat_controller.services.interaction_classifier import InteractionClassifier
from ai_cat_controller.services.tag_engine import TagEngine


class GrowthService:
    def __init__(self, repository: SQLiteRepository) -> None:
        self._repository = repository
        self._classifier = InteractionClassifier()
        self._engine = GrowthEngine()
        self._tags = TagEngine()
        self._behavior = BehaviorProfileService()
        self._lock = asyncio.Lock()

    async def record_dialog(
        self,
        *,
        user_id: str,
        pet_id: str,
        conversation_id: str,
        user_content: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        classification = self._classifier.classify(user_content)
        return await self.record(
            user_id=user_id,
            pet_id=pet_id,
            source_type=GrowthSourceType.DIALOG,
            source_id=conversation_id,
            classification=classification,
            metadata={"conversation_id": conversation_id, **dict(metadata or {})},
        )

    async def record_interaction(
        self,
        *,
        user_id: str,
        pet_id: str,
        source_id: str,
        interaction_type: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        details = dict(metadata or {})
        if interaction_type == "touch":
            sensor = str(details.get("sensor", "unknown"))
            source_type = GrowthSourceType.TOUCH
            classification = GrowthClassification(
                event_type=GrowthEventType.PHYSICAL_TOUCH,
                topic=f"touch:{sensor}"[:64],
                emotion=GrowthEmotion.POSITIVE,
                engagement=1.0,
            )
        elif interaction_type == "completed_task":
            source_type = GrowthSourceType.TASK
            classification = GrowthClassification(
                event_type=GrowthEventType.COMPLETED_TASK,
                topic="task",
                emotion=GrowthEmotion.POSITIVE,
                engagement=1.0,
            )
        elif interaction_type in {"daily_meeting", "daily_check_in"}:
            source_type = GrowthSourceType.DAILY_MEETING
            classification = GrowthClassification(
                event_type=GrowthEventType.DAILY_MEETING,
                topic="daily_meeting",
                emotion=GrowthEmotion.POSITIVE,
                engagement=1.0,
            )
        else:
            return None
        return await self.record(
            user_id=user_id,
            pet_id=pet_id,
            source_type=source_type,
            source_id=source_id,
            classification=classification,
            metadata=details,
        )

    async def record(
        self,
        *,
        user_id: str,
        pet_id: str,
        source_type: GrowthSourceType,
        source_id: str,
        classification: GrowthClassification,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not source_id or len(source_id) > 256:
            raise ValueError("growth source_id must contain 1..256 characters")
        async with self._lock:
            pet = await asyncio.to_thread(
                self._repository.get_owned_pet,
                user_id,
                pet_id,
            )
            attributes_before = await asyncio.to_thread(
                self._repository.get_growth_attributes,
                pet_id,
            )
            tags_before = await asyncio.to_thread(
                self._repository.list_growth_tags,
                pet_id,
            )
            profile_before = self._build_behavior(
                pet,
                attributes_before,
                tags_before,
            )
            recent = await asyncio.to_thread(
                self._repository.list_recent_growth_events,
                pet_id,
                20,
            )
            calculation = self._engine.calculate(
                event_type=classification.event_type,
                personality_id=str(pet["personality_id"]),
                engagement=classification.engagement,
                topic=classification.topic,
                current_attributes=attributes_before,
                recent_events=recent,
                metadata=metadata,
            )
            event = await asyncio.to_thread(
                self._repository.apply_growth_event,
                user_id=user_id,
                pet_id=pet_id,
                source_type=source_type.value,
                source_id=source_id,
                event_type=classification.event_type.value,
                topic=classification.topic,
                emotion=classification.emotion.value,
                engagement=float(calculation["engagement"]),
                novelty=float(calculation["novelty"]),
                repetition_decay=float(calculation["repetition_decay"]),
                attribute_delta=dict(calculation["attribute_delta"]),
                metadata=dict(metadata or {}),
            )
            if event["duplicate"]:
                return {
                    **event,
                    "awarded_tags": [],
                    "behavior_changed": False,
                    "behavior_profile": profile_before,
                }

            attributes_after = dict(event["attributes"])
            counts = await asyncio.to_thread(
                self._repository.growth_event_counts,
                pet_id,
            )
            earned_ids = {str(tag["tag_id"]) for tag in tags_before}
            rules = self._tags.evaluate(
                personality_id=str(pet["personality_id"]),
                intimacy_level=level_for_points(int(pet["intimacy_points"])).level,
                attributes=attributes_after,
                event_counts=counts,
                earned_tag_ids=earned_ids,
            )
            awards = await asyncio.to_thread(
                self._repository.award_growth_tags,
                user_id=user_id,
                pet_id=pet_id,
                triggering_event_id=str(event["event_id"]),
                awards=[
                    {
                        "tag_id": rule.tag_id,
                        "replaces": rule.replaces,
                        "evidence": {
                            "attributes": attributes_after,
                            "event_counts": counts,
                            "personality_id": pet["personality_id"],
                            "intimacy_level": level_for_points(
                                int(pet["intimacy_points"])
                            ).level,
                        },
                    }
                    for rule in rules
                ],
            )
            tags_after = await asyncio.to_thread(
                self._repository.list_growth_tags,
                pet_id,
            )
            profile_after = self._build_behavior(
                pet,
                attributes_after,
                tags_after,
            )
            return {
                **event,
                "awarded_tags": [item["tag_id"] for item in awards],
                "behavior_changed": (
                    profile_before["revision"] != profile_after["revision"]
                ),
                "behavior_profile": profile_after,
            }

    async def behavior_profile(self, pet: Mapping[str, Any]) -> dict[str, Any]:
        attributes, tags = await asyncio.gather(
            asyncio.to_thread(
                self._repository.get_growth_attributes,
                str(pet["pet_id"]),
            ),
            asyncio.to_thread(
                self._repository.list_growth_tags,
                str(pet["pet_id"]),
            ),
        )
        return self._build_behavior(pet, attributes, tags)

    async def record_debug_events(
        self,
        *,
        user_id: str,
        pet_id: str,
        request_id: str,
        event_type: GrowthEventType,
        count: int,
        topic: str,
        emotion: GrowthEmotion,
        engagement: float,
    ) -> dict[str, Any]:
        processed = 0
        duplicates = 0
        awarded_tags: set[str] = set()
        behavior_changed = False
        for index in range(count):
            topic_suffix = f":{index + 1}"
            event_topic = f"{topic[:64 - len(topic_suffix)]}{topic_suffix}"
            classification = GrowthClassification(
                event_type=event_type,
                topic=event_topic,
                emotion=emotion,
                engagement=engagement,
            )
            result = await self.record(
                user_id=user_id,
                pet_id=pet_id,
                source_type=GrowthSourceType.DEBUG,
                source_id=f"{request_id}:{index}",
                classification=classification,
                metadata={"debug": True, "batch_request_id": request_id},
            )
            if result["duplicate"]:
                duplicates += 1
            else:
                processed += 1
            awarded_tags.update(result["awarded_tags"])
            behavior_changed = behavior_changed or bool(
                result["behavior_changed"]
            )
        return {
            "processed": processed,
            "duplicates": duplicates,
            "awarded_tags": sorted(awarded_tags),
            "behavior_changed": behavior_changed,
        }

    def _build_behavior(
        self,
        pet: Mapping[str, Any],
        attributes: Mapping[str, int],
        tags: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._behavior.build(
            personality=PERSONALITY_BY_ID[str(pet["personality_id"])],
            intimacy_level=level_for_points(int(pet["intimacy_points"])).level,
            attributes=attributes,
            active_tag_ids=self._tags.active_tag_ids(tags),
        )

    async def state(
        self,
        *,
        user_id: str,
        pet_id: str,
        recent_limit: int = 20,
        include_debug_values: bool = False,
    ) -> dict[str, Any]:
        pet = await asyncio.to_thread(
            self._repository.get_owned_pet,
            user_id,
            pet_id,
        )
        attributes, tags, recent_events = await asyncio.gather(
            asyncio.to_thread(self._repository.get_growth_attributes, pet_id),
            asyncio.to_thread(self._repository.list_growth_tags, pet_id),
            asyncio.to_thread(
                self._repository.list_owned_growth_events,
                user_id,
                pet_id,
                recent_limit,
            ),
        )
        behavior = self._build_behavior(pet, attributes, tags)
        public_tags = []
        for tag in tags:
            rule = TAG_RULE_BY_ID.get(str(tag["tag_id"]))
            if rule is None:
                continue
            public_tag = {
                "tag_id": tag["tag_id"],
                "status": tag["status"],
                "earned_at": tag["earned_at"],
                "replaced_at": tag["replaced_at"],
                "display_name": rule.display_name,
                "description": rule.description,
                "active": tag["status"] == "active",
            }
            if include_debug_values:
                public_tag["triggering_event_id"] = tag["triggering_event_id"]
                public_tag["evidence"] = tag["evidence"]
            public_tags.append(public_tag)
        public_events = [
            self._public_event(event, include_debug_values)
            for event in recent_events
        ]
        public_attributes = {
            attribute.value: {
                "label": GROWTH_ATTRIBUTE_LABELS[attribute],
                "tendency": behavior["attribute_bands"][attribute.value],
            }
            for attribute in GrowthAttribute
        }
        if include_debug_values:
            for attribute in GrowthAttribute:
                public_attributes[attribute.value]["value"] = round(
                    attributes[attribute.value] / ATTRIBUTE_SCALE,
                    2,
                )
        return {
            "attributes": public_attributes,
            "active_tags": [tag for tag in public_tags if tag["active"]],
            "tag_history": public_tags,
            "recent_events": public_events,
            "behavior_profile": behavior,
            "debug_values_visible": include_debug_values,
        }

    @staticmethod
    def _public_event(
        event: Mapping[str, Any],
        include_debug_values: bool,
    ) -> dict[str, Any]:
        event_type = GrowthEventType(str(event["event_type"]))
        delta = dict(event["attribute_delta"])
        topic = str(event["topic"])
        if event_type == GrowthEventType.TAG_AWARDED:
            rule = TAG_RULE_BY_ID.get(topic)
            if rule is not None:
                topic = rule.display_name
        result = {
            "event_id": event["event_id"],
            "source_type": event["source_type"],
            "event_type": event_type.value,
            "event_label": GROWTH_EVENT_LABELS[event_type],
            "topic": topic,
            "emotion": event["emotion"],
            "changed_attributes": [
                GROWTH_ATTRIBUTE_LABELS[GrowthAttribute(key)]
                for key, value in delta.items()
                if int(value) > 0
            ],
            "created_at": event["created_at"],
        }
        if include_debug_values:
            result["attribute_delta"] = {
                key: round(int(value) / ATTRIBUTE_SCALE, 2)
                for key, value in delta.items()
            }
        return result
