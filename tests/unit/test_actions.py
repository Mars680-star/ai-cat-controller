"""Safe action catalogue rules."""

from ai_cat_controller.domain.actions import (
    ACTION_BY_ID,
    ALL_PERSONALITIES,
    action_is_unlocked,
)


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
