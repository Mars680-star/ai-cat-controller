from pathlib import Path
import subprocess


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = (
    PROJECT_ROOT / "scripts" / "k1_backup_release.sh",
    PROJECT_ROOT / "scripts" / "k1_rollback_release.sh",
    PROJECT_ROOT / "scripts" / "k1_verify_touch_mapping.sh",
)


def test_k1_operational_scripts_have_valid_bash_syntax() -> None:
    for script in SCRIPTS:
        result = subprocess.run(
            ["bash", "-n", str(script)],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr


def test_release_backup_and_rollback_have_integrity_guards() -> None:
    backup = SCRIPTS[0].read_text(encoding="utf-8")
    rollback = SCRIPTS[1].read_text(encoding="utf-8")

    assert "sha256sum" in backup
    assert "product-data.db" in backup
    assert "services.tsv" in backup
    assert 'touch "${BACKUP_DIR}/.complete"' in backup
    assert "sha256sum -c SHA256SUMS" in rollback
    assert "--check" in rollback
    assert "before-rollback" in rollback
    assert "SYSTEMCTL" in rollback
