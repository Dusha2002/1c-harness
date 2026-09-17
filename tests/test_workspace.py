from pathlib import Path

import pytest

from onec_harness.workspace import Workspace, WorkspaceError


def test_workspace_blocks_path_escape(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    workspace.ensure_exists()

    with pytest.raises(WorkspaceError):
        workspace.resolve("../secret.txt")


def test_replace_once_requires_unique_match(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    workspace.write_text("CommonModules/Test/Ext/Module.bsl", "A\nneedle\nB\n")

    workspace.replace_once("CommonModules/Test/Ext/Module.bsl", "needle", "replacement")

    assert "replacement" in workspace.read_text("CommonModules/Test/Ext/Module.bsl")
    assert "needle" not in workspace.read_text("CommonModules/Test/Ext/Module.bsl")


def test_search_is_case_insensitive(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    workspace.write_text("Documents/Order/Ext/ObjectModule.bsl", "Процедура Проведение()\nКонецПроцедуры")

    matches = workspace.search("проведение")

    assert len(matches) == 1
    assert matches[0].line == 1
