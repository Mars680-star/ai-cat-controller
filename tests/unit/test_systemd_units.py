from pathlib import Path


SYSTEMD_DIR = (
    Path(__file__).parents[2]
    / "integrations/volcengine-k1/overlay/examples/low_load_solution/linux_k1/systemd"
)


def test_dialog_service_preserves_runtime_status_across_restart() -> None:
    unit = (SYSTEMD_DIR / "volc-conv-ai.service").read_text(
        encoding="utf-8"
    )

    assert "RuntimeDirectory=ai-cat" in unit
    assert "RuntimeDirectoryPreserve=restart" in unit
    assert "Restart=always" in unit
    assert "EnvironmentFile=-/etc/ai-cat-controller.env" in unit


def test_toy_motor_unit_runs_only_safe_autonomy_worker() -> None:
    unit = (SYSTEMD_DIR / "toy_motor.service").read_text(encoding="utf-8")

    assert "ai_cat_controller.autonomy" in unit
    assert "/usr/bin/toy_control" not in unit
    assert "EnvironmentFile=/etc/ai-cat-controller.env" in unit
