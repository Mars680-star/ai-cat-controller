"""Non-blocking semantic classification for completed user dialog turns."""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import ValidationError

from ai_cat_controller.domain.growth import (
    GrowthClassification,
    GrowthEmotion,
    GrowthEventType,
)

LOGGER = logging.getLogger(__name__)


class InteractionClassifier:
    """Local V1 classifier with a strict, replaceable output contract."""

    _TOPIC_RULES = (
        ("robotics", ("机器人", "电机", "传感器", "机械")),
        ("programming", ("代码", "编程", "python", "api", "程序")),
        ("science", ("科学", "物理", "化学", "实验", "原理")),
        ("learning", ("学习", "知识", "作业", "课程", "考试")),
        ("planning", ("计划", "安排", "目标", "步骤", "任务")),
        ("emotions", ("心情", "难过", "伤心", "焦虑", "压力", "开心")),
        ("humor", ("笑话", "哈哈", "搞笑", "逗我")),
    )

    @staticmethod
    def fallback() -> GrowthClassification:
        return GrowthClassification(
            event_type=GrowthEventType.CASUAL_CHAT,
            topic="general",
            emotion=GrowthEmotion.NEUTRAL,
            engagement=0.5,
        )

    def parse_json(self, payload: str | bytes | dict[str, Any]) -> GrowthClassification:
        try:
            data = json.loads(payload) if isinstance(payload, (str, bytes)) else payload
            return GrowthClassification.model_validate(data)
        except (json.JSONDecodeError, UnicodeError, ValidationError, TypeError):
            LOGGER.warning("growth classification parse failed; using casual_chat")
            return self.fallback()

    def classify(self, content: str) -> GrowthClassification:
        try:
            text = content.strip().lower()
            if not text:
                return self.fallback()
            topic = self._topic(text)
            emotion = self._emotion(text)
            event_type = self._event_type(text, topic)
            engagement = min(1.0, max(0.5, 0.5 + min(len(text), 100) / 200))
            return GrowthClassification(
                event_type=event_type,
                topic=topic,
                emotion=emotion,
                engagement=engagement,
            )
        except Exception:
            LOGGER.exception("growth classification failed; using casual_chat")
            return self.fallback()

    def _topic(self, text: str) -> str:
        for topic, keywords in self._TOPIC_RULES:
            if any(keyword in text for keyword in keywords):
                return topic
        return "general"

    @staticmethod
    def _emotion(text: str) -> GrowthEmotion:
        if any(word in text for word in ("生气", "愤怒", "气死")):
            return GrowthEmotion.ANGRY
        if any(word in text for word in ("难过", "伤心", "失落", "哭")):
            return GrowthEmotion.SAD
        if any(word in text for word in ("焦虑", "压力", "害怕", "担心")):
            return GrowthEmotion.NEGATIVE
        if any(word in text for word in ("太棒", "激动", "兴奋", "成功了")):
            return GrowthEmotion.EXCITED
        if any(word in text for word in ("开心", "高兴", "谢谢", "喜欢")):
            return GrowthEmotion.POSITIVE
        return GrowthEmotion.NEUTRAL

    @staticmethod
    def _event_type(text: str, topic: str) -> GrowthEventType:
        if any(word in text for word in ("难过", "伤心", "焦虑", "压力", "心情")):
            return GrowthEventType.EMOTIONAL_SHARING
        if any(word in text for word in ("加油", "鼓励", "你可以", "会成功")):
            return GrowthEventType.ENCOURAGEMENT
        if any(word in text for word in ("笑话", "哈哈", "搞笑", "逗我")):
            return GrowthEventType.JOKE
        if any(word in text for word in ("计划", "安排", "目标", "下一步")):
            return GrowthEventType.PLANNING
        if topic in {"robotics", "programming", "science", "learning"}:
            return GrowthEventType.KNOWLEDGE_DISCUSSION
        if any(word in text for word in ("什么", "为什么", "怎么", "如何", "吗", "？", "?")):
            return GrowthEventType.QUESTION
        return GrowthEventType.CASUAL_CHAT
