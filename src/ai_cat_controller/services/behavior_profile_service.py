"""Convert stable growth state into prompt-ready behavior directives."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence

from ai_cat_controller.domain.growth import (
    GROWTH_ATTRIBUTE_LABELS,
    TAG_RULE_BY_ID,
    GrowthAttribute,
)
from ai_cat_controller.domain.personalities import PersonalityDefinition


class BehaviorProfileService:
    _BAND_DIRECTIVES = {
        GrowthAttribute.CURIOSITY: "对新知识表现出明显兴趣，适度追问。",
        GrowthAttribute.EMPATHY: "更主动识别并回应用户情绪。",
        GrowthAttribute.KNOWLEDGE: "解释问题时更重视结构和可验证信息。",
        GrowthAttribute.ENERGY: "表达更积极，但避免持续吵闹。",
        GrowthAttribute.MISCHIEF: "偶尔使用友善的俏皮表达。",
        GrowthAttribute.DISCIPLINE: "在任务和计划话题中推动一个明确下一步。",
    }

    _PERSONALITY_TAG_EXPRESSION = {
        "gentle_companion": "使用直接、温柔且不过度承诺的方式表达这些倾向。",
        "proud_star": "保留嘴硬心软的表达方式，不能用傲娇掩盖真实关心。",
        "calm_guardian": "使用冷静可靠的方式表达，并优先给出可执行建议。",
        "sunny_explorer": "使用明快的探索语气表达，但不要把严肃情绪娱乐化。",
        "curious_scholar": "使用清晰可验证的方式表达，避免连续追问。",
    }

    def build(
        self,
        *,
        personality: PersonalityDefinition,
        intimacy_level: int,
        attributes: Mapping[str, int],
        active_tag_ids: Sequence[str],
    ) -> dict[str, object]:
        bands = {
            attribute.value: self._band(int(attributes.get(attribute.value, 0)))
            for attribute in GrowthAttribute
        }
        directives = [
            self._BAND_DIRECTIVES[attribute]
            for attribute in GrowthAttribute
            if bands[attribute.value] in {"明显", "突出"}
        ]
        tag_names: list[str] = []
        for tag_id in active_tag_ids:
            rule = TAG_RULE_BY_ID.get(tag_id)
            if rule is None:
                continue
            tag_names.append(rule.display_name)
            directives.append(rule.directive)
        if directives:
            directives.append(
                self._PERSONALITY_TAG_EXPRESSION[personality.personality_id]
            )
        stable = {
            "personality_id": personality.personality_id,
            "intimacy_level": intimacy_level,
            "attribute_bands": bands,
            "active_tag_ids": list(active_tag_ids),
            "directives": directives,
        }
        revision = hashlib.sha256(
            json.dumps(
                stable,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:24]
        return {
            "revision": revision,
            "base_personality_id": personality.personality_id,
            "base_personality_name": personality.name,
            "intimacy_level": intimacy_level,
            "attribute_bands": bands,
            "attribute_tendencies": {
                GROWTH_ATTRIBUTE_LABELS[attribute]: bands[attribute.value]
                for attribute in GrowthAttribute
            },
            "active_tag_ids": list(active_tag_ids),
            "active_tag_names": tag_names,
            "directives": directives,
        }

    @staticmethod
    def _band(value_units: int) -> str:
        if value_units >= 8000:
            return "突出"
        if value_units >= 6000:
            return "明显"
        if value_units >= 4000:
            return "成长中"
        if value_units >= 2000:
            return "初显"
        return "普通"
