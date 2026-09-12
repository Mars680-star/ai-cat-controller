"""Safe action catalogue rules."""

from ai_cat_controller.domain.actions import (
    ACTION_BY_ID,
    ALL_PERSONALITIES,
    action_is_unlocked,
)
from ai_cat_controller.adapters.base import Capability


def test_tail_wag_unlocks_for_every_personality_at_level_one() -> None:
    action = ACTION_BY_ID["tail_wag"]

    assert action.personality_ids == ALL_PERSONALITIES
    assert all(
        action_is_unlocked(action, personality_id, 1)
        for personality_id in ALL_PERSONALITIES
    )
    assert all(
        not action_is_unlocked(action, personality_id, 0)
        for personality_id in ALL_PERSONALITIES
    )


def test_complex_actions_use_fixed_hardware_presets() -> None:
    assert {
        action_id: ACTION_BY_ID[action_id].hardware_preset
        for action_id in (
            "proud_pose",
            "quiet_companion",
            "greeting_combo",
            "celebration_combo",
        )
    } == {
        "proud_pose": "proud_pose",
        "quiet_companion": "quiet_companion",
        "greeting_combo": "greeting_combo",
        "celebration_combo": "celebration_combo",
    }
    assert tuple(
        component.capability
        for component in ACTION_BY_ID["proud_pose"].components
    ) == (Capability.SHAKE_HEAD, Capability.WAG_TAIL)
