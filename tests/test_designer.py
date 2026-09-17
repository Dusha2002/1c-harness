from pathlib import Path

from onec_harness.onec.designer import Designer
from onec_harness.settings import Settings


def test_split_1c_connection_args() -> None:
    assert Designer._split_args('/F "C:\\1c bases\\demo"') == ["/F", "C:\\1c bases\\demo"]


def test_designer_is_dry_run_by_default(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        onec_exe=tmp_path / "1cv8.exe",
        onec_ib_connection='/F "C:\\demo"',
        onec_workspace=tmp_path / "workspace",
    )
    designer = Designer(settings)

    result = designer.check_modules()

    assert result.executed is False
    assert "/CheckModules" in result.command
    assert "-Server" in result.command
