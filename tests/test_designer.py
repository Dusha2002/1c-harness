from pathlib import Path

import pytest

from onec_harness.onec.designer import Designer, DesignerError
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


def test_primary_auth_is_added_to_designer_command(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        onec_exe=tmp_path / "1cv8.exe",
        onec_ib_connection='/F "C:\\demo"',
        onec_user="Admin",
        onec_password="secret",
    )
    command = Designer(settings)._base_command()

    assert "/N" in command
    assert command[command.index("/N") + 1] == "Admin"
    assert "/P" in command
    assert command[command.index("/P") + 1] == "secret"


def test_staging_auth_does_not_inherit_primary_credentials(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        onec_exe=tmp_path / "1cv8.exe",
        onec_ib_connection='/F "C:\\primary"',
        onec_staging_ib_connection='/F "C:\\staging"',
        onec_user="PrimaryUser",
        onec_password="primary-secret",
    )
    command = Designer(
        settings,
        connection_override=settings.onec_staging_ib_connection,
        auth_kind="staging",
    )._base_command()

    assert "PrimaryUser" not in command
    assert "primary-secret" not in command
    assert "/N" not in command
    assert "/P" not in command


def test_staging_auth_uses_its_own_credentials(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        onec_exe=tmp_path / "1cv8.exe",
        onec_ib_connection='/F "C:\\primary"',
        onec_staging_ib_connection='/F "C:\\staging"',
        onec_staging_user="SandboxUser",
        onec_staging_password="sandbox-secret",
    )
    command = Designer(
        settings,
        connection_override=settings.onec_staging_ib_connection,
        auth_kind="staging",
    )._base_command()

    assert "SandboxUser" in command
    assert "sandbox-secret" in command


def test_monitored_export_reports_progress(tmp_path: Path, monkeypatch) -> None:
    designer = _designer(tmp_path)
    designer.exe.write_bytes(b"fake")
    progress: list[float] = []

    class FakeProcess:
        def __init__(self, *args, **kwargs):
            self.returncode = None
            self.polls = 0

        def poll(self):
            self.polls += 1
            if self.polls >= 2:
                self.returncode = 0
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr("onec_harness.onec.designer.subprocess.Popen", FakeProcess)

    result = designer.dump_config(
        tmp_path / "export",
        execute=True,
        progress=progress.append,
        timeout_seconds=5,
    )

    assert result.ok
    assert progress


def test_monitored_export_can_be_cancelled(tmp_path: Path, monkeypatch) -> None:
    designer = _designer(tmp_path)
    designer.exe.write_bytes(b"fake")

    class FakeProcess:
        def __init__(self, *args, **kwargs):
            self.returncode = None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr("onec_harness.onec.designer.subprocess.Popen", FakeProcess)

    with pytest.raises(DesignerError, match="отменена"):
        designer.dump_config(
            tmp_path / "export",
            execute=True,
            progress=lambda elapsed: None,
            cancelled=lambda: True,
            timeout_seconds=5,
        )
