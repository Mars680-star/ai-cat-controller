"""Bounded public product views; no device/session/debug identifiers cross here."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

EventType = Literal["daily_check_in", "valid_dialog", "touch", "completed_task", "ignored_greeting"]
WritableEvent = Literal["daily_check_in", "touch", "completed_task", "ignored_greeting"]
PersonalityId = Literal["sunny_explorer", "gentle_companion", "proud_star", "curious_scholar", "calm_guardian"]
AttributeId = Literal["curiosity", "empathy", "knowledge", "energy", "mischief", "discipline"]
ActionId = Literal["head_shake", "head_nod", "tail_wag", "proud_pose", "quiet_companion", "greeting_combo", "celebration_combo"]
ShortText = Annotated[str, Field(max_length=80)]
UnlockText = Annotated[str, Field(max_length=160)]


class View(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Personality(View):
    personalityId: PersonalityId
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(max_length=500)
    languageStyle: str = Field(max_length=500)
    voiceName: str = Field(max_length=80)


class Intimacy(View):
    points: int = Field(ge=0, le=1_000_000_000)
    level: int = Field(ge=0, le=4)
    levelName: str = Field(min_length=1, max_length=80)
    minPoints: int = Field(ge=0, le=1_000_000_000)
    nextPoints: int | None = Field(ge=0, le=1_000_000_000)
    progressPercent: float = Field(ge=0, le=100)
    address: str = Field(min_length=1, max_length=80)
    currentUnlocks: list[UnlockText] = Field(max_length=32)
    nextUnlocks: list[UnlockText] = Field(max_length=32)


class Interaction(View):
    eventType: EventType
    label: str = Field(min_length=1, max_length=80)
    pointsDelta: int = Field(ge=-100, le=100)
    pointsAfter: int = Field(ge=0, le=1_000_000_000)
    createdAt: str = Field(min_length=20, max_length=40)


class SampledView(View):
    sampledAt: str = Field(min_length=20, max_length=40)


class Today(View):
    interactionCount: None = None
    pointsGained: None = None


class Dashboard(SampledView):
    petName: str = Field(min_length=1, max_length=80)
    personality: Personality
    intimacy: Intimacy
    unlockedActionIds: list[ActionId] = Field(max_length=7)
    recentInteractions: list[Interaction] = Field(max_length=30)
    historyScope: Literal["latest_30"]
    today: Today
    interactionsEnabled: bool


class Rule(View):
    eventType: EventType
    label: str = Field(min_length=1, max_length=80)
    points: int = Field(ge=-100, le=100)
    maxPerDay: int = Field(ge=0, le=1_000_000)


class IntimacyView(SampledView):
    intimacy: Intimacy
    dailyGrowthCap: int = Field(ge=0, le=1_000_000)
    dailyCheckInCompleted: bool
    rules: list[Rule] = Field(min_length=5, max_length=5)
    history: list[Interaction] = Field(max_length=30)
    historyScope: Literal["latest_30"]
    interactionsEnabled: bool


class Attribute(View):
    attributeId: AttributeId
    label: str = Field(min_length=1, max_length=80)
    tendency: Literal["普通", "初显", "成长中", "明显", "突出"]
    value: float = Field(ge=0, le=100)


class ActiveTag(View):
    tagId: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(max_length=500)
    earnedAt: str = Field(min_length=20, max_length=40)


class Tag(ActiveTag):
    active: bool
    replacedAt: str | None = Field(min_length=20, max_length=40)


class GrowthEvent(View):
    eventType: Literal["knowledge_discussion", "question", "emotional_sharing", "joke", "casual_chat", "planning", "encouragement", "physical_touch", "completed_task", "daily_meeting", "tag_awarded"]
    label: str = Field(min_length=1, max_length=80)
    topic: str = Field(max_length=160)
    emotion: Literal["positive", "neutral", "negative", "excited", "sad", "angry"]
    changedAttributes: list[AttributeId] = Field(max_length=6)
    createdAt: str = Field(min_length=20, max_length=40)


class Behavior(View):
    basePersonalityId: PersonalityId
    basePersonalityName: str = Field(min_length=1, max_length=80)
    intimacyLevel: int = Field(ge=0, le=4)
    activeTagNames: list[ShortText] = Field(max_length=64)
    directives: list[str] = Field(max_length=32)


class Growth(SampledView):
    enabled: bool
    attributes: list[Attribute] = Field(min_length=6, max_length=6)
    activeTags: list[ActiveTag] = Field(max_length=64)
    tagHistory: list[Tag] = Field(max_length=64)
    recentEvents: list[GrowthEvent] = Field(max_length=20)
    behavior: Behavior


class InteractionRequest(View):
    eventType: WritableEvent
    requestId: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")


class Receipt(View):
    receiptId: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")
    eventType: WritableEvent
    requestId: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")
    status: Literal["sending", "recorded", "unknown", "failed"]
    pointsDelta: int | None = Field(ge=-100, le=100)
    pointsAfter: int | None = Field(ge=0, le=1_000_000_000)
    duplicate: bool | None
    celebrationScheduled: bool | None
    updatedAt: str = Field(min_length=20, max_length=40)


def validate_public(model, value):
    """Validate dates and bounded arrays as well as the ordinary strict schema."""
    result = model.model_validate(value).model_dump()

    def visit(node, key=""):
        if isinstance(node, dict):
            for child_key, child in node.items():
                visit(child, child_key)
        elif isinstance(node, list):
            for child in node:
                visit(child, key)
        elif isinstance(node, str):
            if len(node) > 500 or any(ord(c) < 32 or 127 <= ord(c) <= 159
                                     or 0x202A <= ord(c) <= 0x202E or 0x2066 <= ord(c) <= 0x2069 for c in node):
                raise ValueError("Invalid public text")
            if key.endswith("At"):
                if not node.endswith("Z"):
                    raise ValueError("Invalid public timestamp")
                datetime.fromisoformat(node[:-1] + "+00:00")

    visit(result)
    return result
