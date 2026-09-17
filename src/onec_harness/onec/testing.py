from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from onec_harness.settings import Settings
from onec_harness.workspace import Workspace


class TestClientError(RuntimeError):
    pass


@dataclass(slots=True)
class TestProcess:
    command: list[str]
    pid: int | None = None
    executed: bool = False


class TestClientLauncher:
    """Launch standard 1C test-manager/test-client modes.

    The Test Client intentionally targets ONEC_TEST_CLIENT_CONNECTION or the
    staging infobase. It never silently falls back to the primary development DB.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if settings.onec_exe is None:
            raise TestClientError("ONEC_EXE is not configured")
        self.exe = settings.onec_exe.expanduser()

    @staticmethod
    def _split_args(raw: str) -> list[str]:
        tokens = re.findall(r'"[^"]*"|\S+', raw)
        return [token[1:-1] if len(token) >= 2 and token[0] == token[-1] == '"' else token for token in tokens]

    def _enterprise_command(self, connection: str, user: str | None, password: str | None) -> list[str]:
        command = [str(self.exe), "ENTERPRISE"]
        command.extend(self._split_args(connection))
        if user:
            command.extend(["/N", user])
        if password:
            command.extend(["/P", password])
        command.extend(["/DisableStartupMessages", "/DisableStartupDialogs"])
        return command

    def test_client_command(self) -> list[str]:
        connection = self.settings.onec_test_client_connection.strip() or self.settings.onec_staging_ib_connection.strip()
        if not connection:
            raise TestClientError("Configure ONEC_TEST_CLIENT_CONNECTION or ONEC_STAGING_IB_CONNECTION")
        command = self._enterprise_command(connection, self.settings.onec_user, self.settings.onec_password)
        command.extend(["/TestClient", f"-TPort{self.settings.onec_test_port}"])
        if self.settings.onec_test_client_id:
            command.append(f"-TestClientID{self.settings.onec_test_client_id}")
        return command

    def test_manager_command(self) -> list[str]:
        connection = self.settings.onec_test_manager_connection.strip()
        if not connection:
            raise TestClientError("ONEC_TEST_MANAGER_CONNECTION is not configured")
        command = self._enterprise_command(
            connection,
            self.settings.onec_test_manager_user or self.settings.onec_user,
            self.settings.onec_test_manager_password or self.settings.onec_password,
        )
        command.append("/TestManager")
        return command

    def _launch(self, command: list[str], *, execute: bool) -> TestProcess:
        if not execute:
            return TestProcess(command=command, pid=None, executed=False)
        if not self.exe.exists():
            raise TestClientError(f"1C executable not found: {self.exe}")
        try:
            process = subprocess.Popen(command)  # noqa: S603
        except OSError as exc:
            raise TestClientError(f"Cannot start 1C test process: {exc}") from exc
        return TestProcess(command=command, pid=process.pid, executed=True)

    def launch_client(self, *, execute: bool = False) -> TestProcess:
        return self._launch(self.test_client_command(), execute=execute)

    def launch_manager(self, *, execute: bool = False) -> TestProcess:
        return self._launch(self.test_manager_command(), execute=execute)


class ScenarioCompiler:
    """Compile a tiny JSON-like UI action DSL into 1C Test Manager BSL.

    Supported actions map to the logical UI object model: execute_command,
    wait_form, set_field, click_button and assert_field.
    """

    def __init__(self, host: str = "localhost", port: int = 1538) -> None:
        if not 1 <= port <= 65535:
            raise TestClientError("test port must be between 1 and 65535")
        self.host = host
        self.port = port

    @staticmethod
    def _string(value: Any) -> str:
        return str(value).replace('"', '""')

    def compile(self, actions: Sequence[dict[str, Any]], *, procedure_name: str = "RunHarnessScenario") -> str:
        if not re.fullmatch(r"[A-Za-zА-Яа-яЁё_][A-Za-zА-Яа-яЁё0-9_]*", procedure_name):
            raise TestClientError("Invalid BSL procedure name")
        constructor = (
            f'New TestedApplication("{self._string(self.host)}")'
            if self.port == 1538
            else f'New TestedApplication("{self._string(self.host)}", {self.port})'
        )
        lines = [
            f"Procedure {procedure_name}() Export",
            f"    TestedApplication = {constructor};",
            "    TestedApplication.Connect();",
            '    MainWindow = TestedApplication.FindObject(Type("TestedClientApplicationWindow"));',
            "    MainWindow.Activate();",
            "    CurrentForm = Undefined;",
            "",
        ]
        for index, action in enumerate(actions, start=1):
            kind = str(action.get("action", "")).strip().lower()
            if kind == "execute_command":
                link = self._string(action.get("link", ""))
                if not link:
                    raise TestClientError(f"action #{index}: link is required")
                lines.append(f'    MainWindow.ExecuteCommand("{link}");')
            elif kind == "wait_form":
                title = self._string(action.get("title", ""))
                if not title:
                    raise TestClientError(f"action #{index}: title is required")
                lines.extend(
                    [
                        f'    If Not TestedApplication.WaitForObjectDisplayed(Type("TestedForm"), "{title}") Then',
                        f'        Raise "Harness: form did not appear: {title}";',
                        "    EndIf;",
                        f'    CurrentForm = TestedApplication.FindObject(Type("TestedForm"), "{title}");',
                        "    CurrentForm.Activate();",
                    ]
                )
            elif kind == "set_field":
                name = self._string(action.get("name", ""))
                value = self._string(action.get("value", ""))
                if not name:
                    raise TestClientError(f"action #{index}: field name is required")
                lines.extend(
                    [
                        "    If CurrentForm = Undefined Then Raise \"Harness: no active tested form\"; EndIf;",
                        f'    Field = CurrentForm.FindObject(Type("TestedFormField"), "{name}");',
                        "    Field.Activate();",
                        f'    Field.InputText("{value}");',
                    ]
                )
            elif kind == "click_button":
                title = self._string(action.get("title", ""))
                if not title:
                    raise TestClientError(f"action #{index}: button title is required")
                lines.extend(
                    [
                        "    If CurrentForm = Undefined Then Raise \"Harness: no active tested form\"; EndIf;",
                        f'    Button = CurrentForm.FindObject(Type("TestedFormButton"), "{title}");',
                        "    Button.Click();",
                    ]
                )
            elif kind == "assert_field":
                name = self._string(action.get("name", ""))
                expected = self._string(action.get("value", ""))
                if not name:
                    raise TestClientError(f"action #{index}: field name is required")
                lines.extend(
                    [
                        "    If CurrentForm = Undefined Then Raise \"Harness: no active tested form\"; EndIf;",
                        f'    Field = CurrentForm.FindObject(Type("TestedFormField"), "{name}");',
                        f'    If Field.GetEditText() <> "{expected}" Then',
                        f'        Raise "Harness assertion failed for field {name}";',
                        "    EndIf;",
                    ]
                )
            else:
                raise TestClientError(f"action #{index}: unsupported action {kind!r}")
            lines.append("")

        lines.extend(["    TestedApplication.Disconnect();", "EndProcedure"])
        return "\n".join(lines) + "\n"

    def write(
        self,
        workspace: Workspace,
        actions: Sequence[dict[str, Any]],
        *,
        relative_path: str | Path = ".onec-harness/test-scenarios/generated.bsl",
    ) -> Path:
        path = workspace.resolve(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.compile(actions), encoding="utf-8")
        return path
