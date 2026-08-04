from ai_cat_controller.api.personality import native_personality_is_applied


def test_personality_requires_matching_live_native_revision() -> None:
    profile = {"revision": "revision-a"}

    assert native_personality_is_applied(
        profile,
        {
            "personality_revision": "revision-a",
            "state": "ready",
            "stale": False,
        },
    )
    assert not native_personality_is_applied(
        profile,
        {
            "personality_revision": "revision-a",
            "state": "offline",
            "stale": False,
        },
    )
    assert not native_personality_is_applied(
        profile,
        {
            "personality_revision": "revision-b",
            "state": "ready",
            "stale": False,
        },
    )
