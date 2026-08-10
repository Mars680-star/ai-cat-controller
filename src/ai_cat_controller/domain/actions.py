"""Safe preset action catalogue used by the product Mock."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_cat_controller.adapters.base import Capability


class ActionComponent(BaseModel):
    model_config = ConfigDict(frozen=True)

    capability: Capability
    actuator: Literal["head_lr", "head_ud", "tail_lr"]
    direction: Literal["left", "right", "up", "down", "alternate"]
    angle_degrees: float = Field(ge=0.0, le=45.0)
    speed: float = Field(ge=0.1, le=1.0)
    duration_ms: int = Field(ge=100, le=3000)


class ActionDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    action_id: str
    action_no: int
    name: str
    description: str
    personality_ids: frozenset[str]
    min_intimacy_level: int = Field(ge=0, le=4)
    components: tuple[ActionComponent, ...]

    @property
    def total_duration_ms(self) -> int:
        return sum(component.duration_ms for component in self.components)


ALL_PERSONALITIES = frozenset(
    {
        "sunny_explorer",
        "gentle_companion",
        "proud_star",
        "curious_scholar",
        "calm_guardian",
    }
)

ACTIONS = (
    ActionDefinition(
        action_id="head_shake",
        action_no=1,
        name="轻轻摇头",
        description="头部左右小幅摆动。",
        personality_ids=ALL_PERSONALITIES,
        min_intimacy_level=0,
        components=(
            ActionComponent(
                capability=Capability.SHAKE_HEAD,
                actuator="head_lr",
                direction="alternate",
                angle_degrees=15,
                speed=0.4,
                duration_ms=600,
            ),
        ),
    ),
    ActionDefinition(
        action_id="head_nod",
        action_no=2,
        name="认真点头",
        description="头部上下轻点一次。",
        personality_ids=ALL_PERSONALITIES,
        min_intimacy_level=0,
        components=(
            ActionComponent(
                capability=Capability.NOD_HEAD,
                actuator="head_ud",
                direction="alternate",
                angle_degrees=12,
                speed=0.35,
                duration_ms=600,
            ),
        ),
    ),
    ActionDefinition(
        action_id="tail_wag",
        action_no=3,
        name="开心摇尾",
        description="尾巴左右摆动表达亲近。",
        personality_ids=ALL_PERSONALITIES,
        min_intimacy_level=1,
        components=(
            ActionComponent(
                capability=Capability.WAG_TAIL,
                actuator="tail_lr",
                direction="alternate",
                angle_degrees=25,
                speed=0.5,
                duration_ms=800,
            ),
        ),
    ),
    ActionDefinition(
        action_id="proud_pose",
        action_no=4,
        name="骄傲转身",
        description="轻摇头后收尾，表现克制的得意。",
        personality_ids=frozenset({"proud_star", "calm_guardian"}),
        min_intimacy_level=1,
        components=(
            ActionComponent(
                capability=Capability.SHAKE_HEAD,
                actuator="head_lr",
                direction="right",
                angle_degrees=18,
                speed=0.35,
                duration_ms=500,
            ),
        ),
    ),
    ActionDefinition(
        action_id="quiet_companion",
        action_no=5,
        name="安静陪伴",
        description="缓慢点头，传达正在倾听。",
        personality_ids=frozenset({"gentle_companion", "calm_guardian"}),
        min_intimacy_level=1,
        components=(
            ActionComponent(
                capability=Capability.NOD_HEAD,
                actuator="head_ud",
                direction="down",
                angle_degrees=10,
                speed=0.25,
                duration_ms=900,
            ),
        ),
    ),
    ActionDefinition(
        action_id="greeting_combo",
        action_no=6,
        name="见面问候",
        description="点头后摇尾的组合动作。",
        personality_ids=ALL_PERSONALITIES,
        min_intimacy_level=2,
        components=(
            ActionComponent(
                capability=Capability.NOD_HEAD,
                actuator="head_ud",
                direction="alternate",
                angle_degrees=12,
                speed=0.4,
                duration_ms=500,
            ),
            ActionComponent(
                capability=Capability.WAG_TAIL,
                actuator="tail_lr",
                direction="alternate",
                angle_degrees=22,
                speed=0.5,
                duration_ms=700,
            ),
        ),
    ),
    ActionDefinition(
        action_id="celebration_combo",
        action_no=7,
        name="升级庆祝",
        description="摇头、点头和摇尾组成的庆祝动作。",
        personality_ids=ALL_PERSONALITIES,
        min_intimacy_level=3,
        components=(
            ActionComponent(
                capability=Capability.SHAKE_HEAD,
                actuator="head_lr",
                direction="alternate",
                angle_degrees=20,
                speed=0.55,
                duration_ms=500,
            ),
            ActionComponent(
                capability=Capability.NOD_HEAD,
                actuator="head_ud",
                direction="alternate",
                angle_degrees=15,
                speed=0.45,
                duration_ms=500,
            ),
            ActionComponent(
                capability=Capability.WAG_TAIL,
                actuator="tail_lr",
                direction="alternate",
                angle_degrees=30,
                speed=0.65,
                duration_ms=900,
            ),
        ),
    ),
)

ACTION_BY_ID = {action.action_id: action for action in ACTIONS}


def action_is_unlocked(
    action: ActionDefinition, personality_id: str, intimacy_level: int
) -> bool:
    return (
        personality_id in action.personality_ids
        and intimacy_level >= action.min_intimacy_level
    )
