from pathlib import Path

import pytest

from onec_harness.onec.testing import ScenarioCompiler, TestClientError, TestClientLauncher
from onec_harness.settings import Settings


def test_test_client_uses_staging_and_standard_flags(tmp_path: Path) -> None:
    settings = Settings(
        onec_exe=tmp_path / "1cv8.exe",
        onec_staging_ib_connection='/F "C:\\1c\\stage"',
        onec_test_port=1540,
        onec_test_client_id="harness",
    )

    command = TestClientLauncher(settings).test_client_command()

    assert command[1] == "ENTERPRISE"
    assert "/TestClient" in command
    assert "-TPort1540" in command
    assert "-TestClientIDharness" in command
    assert "C:\\1c\\stage" in command


def test_test_client_never_falls_back_to_primary(tmp_path: Path) -> None:
    settings = Settings(onec_exe=tmp_path / "1cv8.exe", onec_ib_connection='/F "C:\\1c\\primary"')

    with pytest.raises(TestClientError, match="ONEC_TEST_CLIENT_CONNECTION"):
        TestClientLauncher(settings).test_client_command()


def test_scenario_compiler_uses_logical_ui_objects() -> None:
    compiler = ScenarioCompiler("localhost", 1538)

    bsl = compiler.compile(
        [
            {"action": "execute_command", "link": "e1cib/command/Catalog.Оборудование.Create"},
            {"action": "wait_form", "title": "Оборудование*"},
            {"action": "set_field", "name": "Наименование", "value": "Тест"},
            {"action": "click_button", "title": "Записать и закрыть"},
        ]
    )

    assert 'New TestedApplication("localhost")' in bsl
    assert "WaitForObjectDisplayed" in bsl
    assert 'Type("TestedFormField")' in bsl
    assert 'InputText("Тест")' in bsl
    assert "Button.Click()" in bsl


def test_test_client_uses_staging_credentials_not_primary(tmp_path: Path) -> None:
    settings = Settings(
        onec_exe=tmp_path / "1cv8.exe",
        onec_ib_connection='/F "C:\\1c\\primary"',
        onec_staging_ib_connection='/F "C:\\1c\\stage"',
        onec_user="PrimaryUser",
        onec_password="primary-secret",
        onec_staging_user="StageUser",
        onec_staging_password="stage-secret",
    )

    command = TestClientLauncher(settings).test_client_command()

    assert "StageUser" in command
    assert "stage-secret" in command
    assert "PrimaryUser" not in command
    assert "primary-secret" not in command
