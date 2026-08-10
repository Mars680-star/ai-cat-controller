"""Configuration and immutable models for long-term personality growth."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


ATTRIBUTE_SCALE = 100
ATTRIBUTE_MAX_UNITS = 100 * ATTRIBUTE_SCALE


class GrowthAttribute(StrEnum):
    CURIOSITY = "curiosity"
    EMPATHY = "empathy"
    KNOWLEDGE = "knowledge"
    ENERGY = "energy"
    MISCHIEF = "mischief"
    DISCIPLINE = "discipline"


class GrowthSourceType(StrEnum):
    DIALOG = "dialog"
    TOUCH = "touch"
    TASK = "task"
    DAILY_MEETING = "daily_meeting"
    SYSTEM = "system"
    DEBUG = "debug"


class GrowthEventType(StrEnum):
    KNOWLEDGE_DISCUSSION = "knowledge_discussion"
    QUESTION = "question"
    EMOTIONAL_SHARING = "emotional_sharing"
    JOKE = "joke"
    CASUAL_CHAT = "casual_chat"
    PLANNING = "planning"
    ENCOURAGEMENT = "encouragement"
    PHYSICAL_TOUCH = "physical_touch"
    COMPLETED_TASK = "completed_task"
    DAILY_MEETING = "daily_meeting"
    TAG_AWARDED = "tag_awarded"


class GrowthEmotion(StrEnum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    EXCITED = "excited"
    SAD = "sad"
    ANGRY = "angry"


class GrowthClassification(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_type: GrowthEventType
    topic: str = Field(min_length=1, max_length=64)
    emotion: GrowthEmotion
    engagement: float = Field(ge=0.0, le=1.0)


class EventCountRequirement(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_types: tuple[GrowthEventType, ...]
    minimum: int = Field(ge=1)


class TagRule(BaseModel):
    model_config = ConfigDict(frozen=True)

    tag_id: str
    display_name: str
    description: str
    required_attributes: dict[GrowthAttribute, int]
    required_event_counts: tuple[EventCountRequirement, ...]
    required_personality_ids: tuple[str, ...] = ()
    required_intimacy_level: int | None = None
    replaces: tuple[str, ...] = ()
    directive: str


GROWTH_ATTRIBUTE_LABELS = {
    GrowthAttribute.CURIOSITY: "好奇",
    GrowthAttribute.EMPATHY: "共情",
    GrowthAttribute.KNOWLEDGE: "求知",
    GrowthAttribute.ENERGY: "活力",
    GrowthAttribute.MISCHIEF: "淘气",
    GrowthAttribute.DISCIPLINE: "自律",
}

GROWTH_EVENT_LABELS = {
    GrowthEventType.KNOWLEDGE_DISCUSSION: "知识讨论",
    GrowthEventType.QUESTION: "提出问题",
    GrowthEventType.EMOTIONAL_SHARING: "情绪分享",
    GrowthEventType.JOKE: "轻松玩笑",
    GrowthEventType.CASUAL_CHAT: "日常闲聊",
    GrowthEventType.PLANNING: "计划讨论",
    GrowthEventType.ENCOURAGEMENT: "鼓励互动",
    GrowthEventType.PHYSICAL_TOUCH: "实体触摸",
    GrowthEventType.COMPLETED_TASK: "完成任务",
    GrowthEventType.DAILY_MEETING: "每日见面",
    GrowthEventType.TAG_AWARDED: "获得成长标签",
}

# Values use hundredths of one displayed point. Growth is intentionally slow.
EVENT_BASE_GROWTH: dict[GrowthEventType, dict[GrowthAttribute, int]] = {
    GrowthEventType.KNOWLEDGE_DISCUSSION: {
        GrowthAttribute.CURIOSITY: 100,
        GrowthAttribute.KNOWLEDGE: 140,
    },
    GrowthEventType.QUESTION: {GrowthAttribute.CURIOSITY: 100},
    GrowthEventType.EMOTIONAL_SHARING: {GrowthAttribute.EMPATHY: 130},
    GrowthEventType.JOKE: {
        GrowthAttribute.MISCHIEF: 110,
        GrowthAttribute.ENERGY: 70,
    },
    GrowthEventType.CASUAL_CHAT: {GrowthAttribute.ENERGY: 10},
    GrowthEventType.PLANNING: {
        GrowthAttribute.DISCIPLINE: 130,
        GrowthAttribute.KNOWLEDGE: 30,
    },
    GrowthEventType.ENCOURAGEMENT: {
        GrowthAttribute.EMPATHY: 100,
        GrowthAttribute.DISCIPLINE: 70,
    },
    GrowthEventType.PHYSICAL_TOUCH: {
        GrowthAttribute.EMPATHY: 20,
        GrowthAttribute.ENERGY: 10,
    },
    GrowthEventType.COMPLETED_TASK: {
        GrowthAttribute.DISCIPLINE: 140,
        GrowthAttribute.KNOWLEDGE: 20,
    },
    GrowthEventType.DAILY_MEETING: {
        GrowthAttribute.ENERGY: 40,
        GrowthAttribute.EMPATHY: 20,
    },
    GrowthEventType.TAG_AWARDED: {},
}

TOUCH_GROWTH_OVERRIDES: dict[str, dict[GrowthAttribute, int]] = {
    "head": {GrowthAttribute.EMPATHY: 25, GrowthAttribute.ENERGY: 10},
    "back": {GrowthAttribute.EMPATHY: 20, GrowthAttribute.ENERGY: 10},
    "nose": {GrowthAttribute.MISCHIEF: 25, GrowthAttribute.ENERGY: 15},
    "left_foot": {GrowthAttribute.ENERGY: 20, GrowthAttribute.MISCHIEF: 15},
    "right_foot": {GrowthAttribute.ENERGY: 20, GrowthAttribute.MISCHIEF: 15},
}

PERSONALITY_GROWTH_BIAS: dict[str, dict[GrowthAttribute, float]] = {
    "sunny_explorer": {
        GrowthAttribute.CURIOSITY: 1.15,
        GrowthAttribute.EMPATHY: 0.95,
        GrowthAttribute.KNOWLEDGE: 1.00,
        GrowthAttribute.ENERGY: 1.25,
        GrowthAttribute.MISCHIEF: 1.05,
        GrowthAttribute.DISCIPLINE: 0.90,
    },
    "gentle_companion": {
        GrowthAttribute.CURIOSITY: 0.90,
        GrowthAttribute.EMPATHY: 1.30,
        GrowthAttribute.KNOWLEDGE: 1.00,
        GrowthAttribute.ENERGY: 0.85,
        GrowthAttribute.MISCHIEF: 0.80,
        GrowthAttribute.DISCIPLINE: 1.15,
    },
    "proud_star": {
        GrowthAttribute.CURIOSITY: 1.00,
        GrowthAttribute.EMPATHY: 0.90,
        GrowthAttribute.KNOWLEDGE: 0.90,
        GrowthAttribute.ENERGY: 1.15,
        GrowthAttribute.MISCHIEF: 1.30,
        GrowthAttribute.DISCIPLINE: 0.90,
    },
    "curious_scholar": {
        GrowthAttribute.CURIOSITY: 1.30,
        GrowthAttribute.EMPATHY: 0.90,
        GrowthAttribute.KNOWLEDGE: 1.30,
        GrowthAttribute.ENERGY: 0.90,
        GrowthAttribute.MISCHIEF: 0.80,
        GrowthAttribute.DISCIPLINE: 1.10,
    },
    "calm_guardian": {
        GrowthAttribute.CURIOSITY: 0.85,
        GrowthAttribute.EMPATHY: 1.15,
        GrowthAttribute.KNOWLEDGE: 1.00,
        GrowthAttribute.ENERGY: 0.80,
        GrowthAttribute.MISCHIEF: 0.80,
        GrowthAttribute.DISCIPLINE: 1.30,
    },
}

REPETITION_DECAY = (1.00, 0.85, 0.70, 0.55, 0.40)
TOPIC_NOVELTY_DECAY = (1.00, 0.85, 0.70, 0.55, 0.40)


TAG_RULES = (
    TagRule(
        tag_id="little_explorer",
        display_name="小小探索者",
        description="开始主动探索新问题。",
        required_attributes={GrowthAttribute.CURIOSITY: 5500},
        required_event_counts=(
            EventCountRequirement(
                event_types=(
                    GrowthEventType.QUESTION,
                    GrowthEventType.KNOWLEDGE_DISCUSSION,
                ),
                minimum=25,
            ),
        ),
        directive="对新知识表现出明显兴趣，并在合适时提出一个简短追问。",
    ),
    TagRule(
        tag_id="explorer",
        display_name="探索家",
        description="长期保持强烈而稳定的探索倾向。",
        required_attributes={GrowthAttribute.CURIOSITY: 7200},
        required_event_counts=(
            EventCountRequirement(
                event_types=(
                    GrowthEventType.QUESTION,
                    GrowthEventType.KNOWLEDGE_DISCUSSION,
                ),
                minimum=60,
            ),
        ),
        replaces=("little_explorer",),
        directive="主动连接相关知识、帮助验证假设，但每轮最多追问一次。",
    ),
    TagRule(
        tag_id="little_scholar",
        display_name="小学者",
        description="逐渐形成稳定的求知习惯。",
        required_attributes={GrowthAttribute.KNOWLEDGE: 5500},
        required_event_counts=(
            EventCountRequirement(
                event_types=(GrowthEventType.KNOWLEDGE_DISCUSSION,),
                minimum=25,
            ),
        ),
        directive="解释知识时优先给出清晰的小步骤和可验证例子。",
    ),
    TagRule(
        tag_id="scholar_cat",
        display_name="学霸猫",
        description="长期积累了丰富的知识讨论经历。",
        required_attributes={GrowthAttribute.KNOWLEDGE: 7500},
        required_event_counts=(
            EventCountRequirement(
                event_types=(GrowthEventType.KNOWLEDGE_DISCUSSION,),
                minimum=60,
            ),
        ),
        replaces=("little_scholar",),
        directive="面对知识问题先归纳核心，再给出严谨但简短的解释。",
    ),
    TagRule(
        tag_id="caring_partner",
        display_name="贴心伙伴",
        description="通过长期交流学会更敏锐地回应情绪。",
        required_attributes={GrowthAttribute.EMPATHY: 6500},
        required_event_counts=(
            EventCountRequirement(
                event_types=(
                    GrowthEventType.EMOTIONAL_SHARING,
                    GrowthEventType.ENCOURAGEMENT,
                ),
                minimum=35,
            ),
        ),
        directive="先确认用户感受，再提供陪伴或一个可执行的小建议。",
    ),
    TagRule(
        tag_id="playful",
        display_name="小调皮",
        description="逐渐形成轻松俏皮的互动习惯。",
        required_attributes={GrowthAttribute.MISCHIEF: 6000},
        required_event_counts=(
            EventCountRequirement(
                event_types=(GrowthEventType.JOKE,),
                minimum=35,
            ),
        ),
        directive="偶尔使用不冒犯用户的轻松玩笑，不连续打岔。",
    ),
    TagRule(
        tag_id="disciplined_partner",
        display_name="自律搭子",
        description="在计划和任务中形成稳定的执行倾向。",
        required_attributes={GrowthAttribute.DISCIPLINE: 6500},
        required_event_counts=(
            EventCountRequirement(
                event_types=(
                    GrowthEventType.PLANNING,
                    GrowthEventType.COMPLETED_TASK,
                ),
                minimum=30,
            ),
        ),
        directive="在任务和计划话题中帮助拆分下一步，不进行空泛催促。",
    ),
    TagRule(
        tag_id="soft_hearted",
        display_name="嘴硬心软",
        description="傲娇表达下形成了稳定而真实的关心。",
        required_attributes={GrowthAttribute.EMPATHY: 6000},
        required_event_counts=(
            EventCountRequirement(
                event_types=(
                    GrowthEventType.EMOTIONAL_SHARING,
                    GrowthEventType.ENCOURAGEMENT,
                ),
                minimum=25,
            ),
        ),
        required_personality_ids=("proud_star",),
        required_intimacy_level=2,
        directive="保留傲娇措辞，但必须在句尾给出明确、尊重边界的关心。",
    ),
)

TAG_RULE_BY_ID = {rule.tag_id: rule for rule in TAG_RULES}
