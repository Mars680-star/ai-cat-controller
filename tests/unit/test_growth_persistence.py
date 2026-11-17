import math

import pytest

from ai_cat_controller.persistence.sqlite_repository import SQLiteRepository


def _bound_pet(repository: SQLiteRepository, *, user_code: str, serial: str) -> tuple[str, str]:
    user = repository.login_user(user_code, user_code)
    pet, _ = repository.bind_pet(
        user_id=user["user_id"],
        serial_number=serial,
        pet_name="小安",
        network_name="Test Wi-Fi",
        personality_id="proud_star",
    )
    return str(user["user_id"]), str(pet["pet_id"])


def _apply(
    repository: SQLiteRepository,
    *,
    user_id: str,
    pet_id: str,
    source_id: str,
    delta: dict[str, int] | None = None,
) -> dict:
    return repository.apply_growth_event(
        user_id=user_id,
        pet_id=pet_id,
        source_type="dialog",
        source_id=source_id,
        event_type="question",
        topic="robotics",
        emotion="neutral",
        engagement=1.0,
        novelty=1.0,
        repetition_decay=1.0,
        attribute_delta=delta or {"curiosity": 100},
        metadata={"test": True},
    )


def test_growth_event_is_idempotent_and_capped(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "growth.db")
    repository.initialize()
    user_id, pet_id = _bound_pet(
        repository,
        user_code="owner",
        serial="K1-GROWTH-A",
    )

    first = _apply(
        repository,
        user_id=user_id,
        pet_id=pet_id,
        source_id="same-dialog",
        delta={"curiosity": 9950},
    )
    duplicate = _apply(
        repository,
        user_id=user_id,
        pet_id=pet_id,
        source_id="same-dialog",
        delta={"curiosity": 9950},
    )
    capped = _apply(
        repository,
        user_id=user_id,
        pet_id=pet_id,
        source_id="next-dialog",
        delta={"curiosity": 500},
    )

    assert first["attributes"]["curiosity"] == 9950
    assert duplicate["duplicate"] is True
    assert duplicate["attributes"]["curiosity"] == 9950
    assert capped["attribute_delta"]["curiosity"] == 50
    assert capped["attributes"]["curiosity"] == 10000


def test_invalid_growth_data_does_not_modify_database(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "invalid-growth.db")
    repository.initialize()
    user_id, pet_id = _bound_pet(
        repository,
        user_code="owner",
        serial="K1-GROWTH-INVALID",
    )

    with pytest.raises(ValueError, match="finite"):
        repository.apply_growth_event(
            user_id=user_id,
            pet_id=pet_id,
            source_type="dialog",
            source_id="invalid-dialog",
            event_type="question",
            topic="robotics",
            emotion="neutral",
            engagement=math.nan,
            novelty=1.0,
            repetition_decay=1.0,
            attribute_delta={"curiosity": 100},
            metadata={},
        )

    assert repository.get_growth_attributes(pet_id)["curiosity"] == 0
    assert repository.list_recent_growth_events(pet_id, 10) == []


def test_growth_is_isolated_by_pet(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "growth-isolation.db")
    repository.initialize()
    user_id, first_pet_id = _bound_pet(
        repository,
        user_code="owner",
        serial="K1-GROWTH-FIRST",
    )
    _, second_pet_id = _bound_pet(
        repository,
        user_code="owner",
        serial="K1-GROWTH-SECOND",
    )

    _apply(
        repository,
        user_id=user_id,
        pet_id=first_pet_id,
        source_id="first-pet-dialog",
    )

    assert repository.get_growth_attributes(first_pet_id)["curiosity"] == 100
    assert repository.get_growth_attributes(second_pet_id)["curiosity"] == 0


def test_advanced_tag_replaces_lower_tag_but_preserves_history(tmp_path) -> None:
    repository = SQLiteRepository(tmp_path / "growth-tags.db")
    repository.initialize()
    user_id, pet_id = _bound_pet(
        repository,
        user_code="owner",
        serial="K1-GROWTH-TAGS",
    )
    event = _apply(
        repository,
        user_id=user_id,
        pet_id=pet_id,
        source_id="tag-trigger",
    )

    repository.award_growth_tags(
        user_id=user_id,
        pet_id=pet_id,
        triggering_event_id=event["event_id"],
        awards=[{"tag_id": "little_explorer", "replaces": (), "evidence": {}}],
    )
    repository.award_growth_tags(
        user_id=user_id,
        pet_id=pet_id,
        triggering_event_id=event["event_id"],
        awards=[
            {
                "tag_id": "explorer",
                "replaces": ("little_explorer",),
                "evidence": {},
            }
        ],
    )

    tags = {tag["tag_id"]: tag for tag in repository.list_growth_tags(pet_id)}
    assert tags["little_explorer"]["status"] == "replaced"
    assert tags["little_explorer"]["replaced_at"] is not None
    assert tags["explorer"]["status"] == "active"
