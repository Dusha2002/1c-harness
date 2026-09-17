from pathlib import Path

from onec_harness.onec.designer import Designer
from onec_harness.settings import Settings


def test_check_config_builds_expected_dry_run_flags(tmp_path: Path) -> None:
    settings = Settings(
        onec_exe=tmp_path / "1cv8.exe",
        onec_ib_connection='/F "C:\\Base"',
    )
    designer = Designer(settings)

    result = designer.check_config(execute=False)

    assert result.executed is False
    assert "/CheckConfig" in result.command
    assert "-ConfigLogIntegrity" in result.command
    assert "-IncorrectReferences" in result.command
    assert "-HandlersExistence" in result.command
    assert "-UnreferenceProcedures" in result.command
