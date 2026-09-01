"""The SIN-70 checklist stays complete and points at real tests and commands."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKLIST = ROOT / "docs" / "MVP_ACCEPTANCE.md"
README = ROOT / "README.md"


def test_acceptance_checklist_covers_every_item():
    text = CHECKLIST.read_text(encoding="utf-8")
    for number in range(1, 15):
        assert f"| {number} |" in text, f"missing checklist row {number}"
    assert "tests/test_mvp_acceptance.py" in text
    assert "scripts/production_smoke.sh" in text
    assert "SIN-97" in text
    assert "SIN-98" in text
    assert "SMOKE_BASE_URL" in text


def test_readme_points_at_the_five_minute_demo_and_checklist():
    text = README.read_text(encoding="utf-8")
    assert "docs/MVP_ACCEPTANCE.md" in text
    assert "Five-minute demo" in text
    assert "python -m app.worker" in text
