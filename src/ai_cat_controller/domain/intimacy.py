"""Intimacy levels, event point rules, daily limits, and unlock metadata.

Maintenance notes:
- Keep level thresholds strictly increasing and action unlock levels aligned
  with ``domain.actions``.
- Event names are persisted in history; changing them requires compatibility
  handling for existing data.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class IntimacyLevel(BaseModel):
    model_config = ConfigDict(frozen=True)

    level: int
    name: str
    min_points: int
    next_points: int | None
    badge: str
    unlocks: tuple[str, ...]


class InteractionRule(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_type: str
    label: str
    points: int
    max_per_day: int


INTIMACY_LEVELS = (
    IntimacyLevel(
        level=0,
        name="初次相遇",
        min_points=0,
        next_points=20,
        badge="NEW",
        unlocks=("基础对话", "基础头部动作"),
    ),
    IntimacyLevel(
        level=1,
        name="逐渐熟悉",
        min_points=20,
        next_points=50,
        badge="FAMILIAR",
        unlocks=("亲近称呼", "基础尾部动作", "每日主动问候"),
    ),
    IntimacyLevel(
        level=2,
        name="亲密伙伴",
        min_points=50,
        next_points=100,
        badge="CLOSE",
        unlocks=("组合动作", "情绪陪伴回应"),
    ),
    IntimacyLevel(
        level=3,
        name="深度羁绊",
        min_points=100,
        next_points=180,
        badge="BONDED",
        unlocks=("庆祝动作", "主动陪伴行为", "专属语气"),
    ),
    IntimacyLevel(
        level=4,
        name="灵魂伙伴",
        min_points=180,
        next_points=None,
        badge="SOULMATE",
        unlocks=("全部动作", "最高亲密称呼", "纪念日问候"),
    ),
)

INTERACTION_RULES = {
    rule.event_type: rule
    for rule in (
        InteractionRule(
            event_type="daily_check_in",
            label="每日见面",
            points=3,
            max_per_day=1,
        ),
        InteractionRule(
            event_type="valid_dialog",
            label="有效对话",
            points=2,
            max_per_day=5,
        ),
        InteractionRule(
            event_type="touch",
            label="触摸反馈",
            points=1,
            max_per_day=8,
        ),
        InteractionRule(
            event_type="completed_task",
            label="完成互动任务",
            points=5,
            max_per_day=2,
        ),
        InteractionRule(
            event_type="ignored_greeting",
            label="长期忽略主动问候",
            points=-1,
            max_per_day=2,
        ),
    )
}


def level_for_points(points: int) -> IntimacyLevel:
    applicable = [level for level in INTIMACY_LEVELS if points >= level.min_points]
    return applicable[-1]


def level_progress(points: int) -> dict[str, int | float | None]:
    level = level_for_points(points)
    if level.next_points is None:
        return {
            "current": points,
            "level_start": level.min_points,
            "next_level": None,
            "percent": 100.0,
        }
    span = level.next_points - level.min_points
    progress = min(max(points - level.min_points, 0), span)
    return {
        "current": points,
        "level_start": level.min_points,
        "next_level": level.next_points,
        "percent": round(progress / span * 100.0, 1),
    }
