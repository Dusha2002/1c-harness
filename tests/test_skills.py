from pathlib import Path

import pytest

from onec_harness.skills import SkillStore


def test_builtin_skills_are_listed_without_loading_bodies(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    catalog = store.catalog()

    assert "skill://onec-engineering" in catalog
    assert "skill://highload-systems" in catalog
    assert "Избегай запросов и циклов N+1" not in catalog


def test_user_skill_import_load_and_delete(tmp_path: Path) -> None:
    source = tmp_path / "review.md"
    source.write_text(
        "---\n"
        "name: careful-review\n"
        "description: Проверяет изменения по чек-листу\n"
        "---\n\n"
        "# Review\n"
        "Сначала сравни инварианты.\n",
        encoding="utf-8",
    )

    store = SkillStore(tmp_path / "skills")
    info = store.import_file(source)
    skill = store.load("skill://careful-review")

    assert info.name == "careful-review"
    assert info.source == "user"
    assert skill.description == "Проверяет изменения по чек-листу"
    assert "Сначала сравни инварианты" in skill.content

    store.delete("careful-review")
    with pytest.raises(ValueError, match="Skill not found"):
        store.load("careful-review")


def test_user_skill_can_override_builtin_then_reveal_builtin_after_delete(tmp_path: Path) -> None:
    source = tmp_path / "onec-engineering.md"
    source.write_text(
        "---\nname: onec-engineering\ndescription: Custom\n---\n\nCustom body\n",
        encoding="utf-8",
    )
    store = SkillStore(tmp_path / "skills")

    store.import_file(source)
    assert store.load("onec-engineering").content == "Custom body"

    store.delete("onec-engineering")
    assert "1C Engineering" in store.load("onec-engineering").content


def test_builtin_skill_cannot_be_deleted(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    with pytest.raises(ValueError, match="Built-in"):
        store.delete("highload-systems")


def test_import_without_frontmatter_slugifies_filename(tmp_path: Path) -> None:
    source = tmp_path / "High Load Review.md"
    source.write_text("# Review\nПроверяй нагрузку.\n", encoding="utf-8")
    store = SkillStore(tmp_path / "skills")

    info = store.import_file(source)

    assert info.name == "High-Load-Review"
    assert "Проверяй нагрузку" in store.load(info.name).content
