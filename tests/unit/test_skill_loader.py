from pathlib import Path
from uuid import UUID

import pytest

from erp_agent.agent.skill_loader import SkillError, SkillRegistry
from erp_agent.memory.session_store import SessionStore
from erp_agent.security.authorization import Principal


def test_select_load_and_path_traversal() -> None:
    skills = SkillRegistry()
    assert [skill.name for skill in skills.list()] == ["fx-exposure-analysis", "erp-data-quality"]
    assert len(skills.select("檢查逾期")) == 2
    assert skills.select("有哪些欄位") == ()
    assert "calc_net_exposure" in skills.load("fx-exposure-analysis")
    assert "POSTED" in skills.reference("fx-exposure-analysis", "references/exposure-rules.md")
    for path in ("references/../../secret.md", "/references/rules.md", "references\\secret.md"):
        with pytest.raises(SkillError):
            skills.reference("fx-exposure-analysis", path)
    with pytest.raises(SkillError):
        skills.load("../secret")


@pytest.mark.parametrize("data", [b"# Missing frontmatter", b"x" * 16_385, b"\xff\xfe"])
def test_reject_invalid_files(tmp_path: Path, data: bytes) -> None:
    root = tmp_path / "custom"
    root.mkdir()
    (root / "SKILL.md").write_bytes(data)
    with pytest.raises(SkillError):
        SkillRegistry(tmp_path, ("custom",))


def test_duplicate_skill_names(tmp_path: Path) -> None:
    for folder in ("first", "second"):
        root = tmp_path / folder
        root.mkdir()
        (root / "SKILL.md").write_text(
            "---\nname: first\ndescription: demo\n---\nBody", encoding="utf-8"
        )
    with pytest.raises(SkillError, match="Duplicate"):
        SkillRegistry(tmp_path, ("first", "second"))


def test_session_isolation_and_ttl(tmp_path: Path) -> None:
    actor = Principal(tenant_id="a", actor_id="user", entity_ids=frozenset({"TW01"}))
    store = SessionStore(str(tmp_path / "state.db"))
    run_id = UUID(int=1)
    store.remember(actor, "shared-id", run_id)
    assert store.get(actor, "shared-id") == run_id
    assert store.get(actor.model_copy(update={"tenant_id": "b"}), "shared-id") is None
    assert store.get(actor.model_copy(update={"actor_id": "other"}), "shared-id") is None
    assert store.get(actor.model_copy(update={"actor_type": "service"}), "shared-id") is None
    expired = SessionStore(str(tmp_path / "expired.db"), ttl_seconds=0)
    expired.remember(actor, "shared-id", run_id)
    assert expired.get(actor, "shared-id") is None
