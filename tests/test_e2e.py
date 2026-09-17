from pathlib import Path

from onec_harness.onec.designer import Designer
from onec_harness.onec.e2e import ExternalScenarioPackage, TestManagerRunner
from onec_harness.onec.testing import ScenarioCompiler, TestClientLauncher
from onec_harness.settings import Settings
from onec_harness.workspace import Workspace


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        onec_exe=tmp_path / "1cv8.exe",
        onec_ib_connection='/F "C:\\1c\\dev"',
        onec_staging_ib_connection='/F "C:\\1c\\staging"',
        onec_test_manager_connection='/F "C:\\1c\\test-manager"',
        onec_workspace=tmp_path / "workspace",
    )


def test_external_scenario_package_contains_on_open_runner(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    workspace.ensure_exists()
    package = ExternalScenarioPackage(workspace, ScenarioCompiler())

    artifacts = package.build_sources(
        [
            {"action": "execute_command", "link": "e1cib/command/Catalog.Оборудование.Create"},
            {"action": "wait_form", "title": "Оборудование*"},
        ]
    )

    root = artifacts.source_xml.read_text(encoding="utf-8-sig")
    form = (artifacts.source_xml.parent / artifacts.name / "Forms" / "Форма" / "Ext" / "Form.xml").read_text(
        encoding="utf-8-sig"
    )
    module = (
        artifacts.source_xml.parent / artifacts.name / "Forms" / "Форма" / "Ext" / "Module.bsl"
    ).read_text(encoding="utf-8-sig")
    assert f"ExternalDataProcessor.{artifacts.name}.Form.Форма" in root
    assert '<Event name="OnOpen">ПриОткрытии</Event>' in form
    assert "Procedure RunHarnessScenario() Export" in module
    assert "Процедура ПриОткрытии(Отказ)" in module
    assert "ЗавершитьРаботуСистемы();" in module
    assert str(artifacts.result_path).replace('"', '""') in module


def test_designer_build_external_processor_is_dry_run(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    source = tmp_path / "runner.xml"
    target = tmp_path / "runner.epf"

    result = Designer(settings, connection_override=settings.onec_staging_ib_connection).build_external_processor(source, target)

    assert result.executed is False
    assert "/LoadExternalDataProcessorOrReportFromFiles" in result.command
    assert str(source.resolve()) in result.command
    assert str(target.resolve()) in result.command


def test_test_manager_command_can_execute_generated_epf(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    launcher = TestClientLauncher(settings)
    epf = tmp_path / "runner.epf"
    log = tmp_path / "runner.log"

    command = launcher.test_manager_command(external_processor=epf, output=log)

    assert "/TestManager" in command
    assert "/Execute" in command
    assert str(epf.resolve()) in command
    assert "/Out" in command
    assert str(log.resolve()) in command


def test_runner_dry_run_builds_artifacts_without_starting_1c(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    workspace = Workspace(settings.onec_workspace)
    workspace.ensure_exists()

    result = TestManagerRunner(settings, workspace).run(
        [{"action": "wait_form", "title": "Главное*"}],
        execute=False,
    )

    assert result.status == "DRY_RUN"
    assert result.success is None
    assert result.client is None
    assert result.manager is None
    assert result.artifacts.source_xml.exists()
    assert not result.artifacts.epf_path.exists()
