from pathlib import Path

from onec_harness.onec.designer import Designer
from onec_harness.settings import Settings


def _designer(tmp_path: Path) -> Designer:
    settings = Settings(
        _env_file=None,
        onec_exe=tmp_path / "1cv8.exe",
        onec_ib_connection='/F "C:\\demo"',
        onec_workspace=tmp_path / "workspace",
    )
    return Designer(settings)


def test_split_1c_connection_args() -> None:
    assert Designer._split_args('/F "C:\\1c bases\\demo"') == ["/F", "C:\\1c bases\\demo"]


def test_designer_is_dry_run_by_default(tmp_path: Path) -> None:
    result = _designer(tmp_path).check_modules()

    assert result.executed is False
    assert "/CheckModules" in result.command
    assert "-Server" in result.command


def test_extension_dump_and_load_flags(tmp_path: Path) -> None:
    designer = _designer(tmp_path)

    dump = designer.dump_config(tmp_path / "ext", extension="МоеРасширение")
    load = designer.load_config(tmp_path / "ext", extension="МоеРасширение", update_dump_info=True)

    assert dump.command[-2:] == ["-Extension", "МоеРасширение"]
    assert "-Extension" in load.command
    assert "МоеРасширение" in load.command
    assert "-updateConfigDumpInfo" in load.command


def test_extension_checks_and_cfe_dump(tmp_path: Path) -> None:
    designer = _designer(tmp_path)

    modules = designer.check_modules(extension="МоеРасширение")
    config = designer.check_config(extension="МоеРасширение")
    applicability = designer.check_extension_applicability("МоеРасширение")
    cfe = designer.dump_cfg(tmp_path / "extension.cfe", extension="МоеРасширение")

    for result in (modules, config, applicability, cfe):
        assert "-Extension" in result.command
        assert "МоеРасширение" in result.command
    assert "/CheckCanApplyConfigurationExtensions" in applicability.command
    assert "/DumpCfg" in cfe.command


def test_create_file_infobase_command_is_isolated_from_primary_connection(tmp_path: Path) -> None:
    designer = _designer(tmp_path)

    result = designer.create_file_infobase(tmp_path / "sandbox")

    assert result.executed is False
    assert result.command[1] == "CREATEINFOBASE"
    assert any(part.startswith('File="') for part in result.command)
    assert "/F" not in result.command
    assert "C:\\demo" not in result.command
