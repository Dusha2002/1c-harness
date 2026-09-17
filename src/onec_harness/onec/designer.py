from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from onec_harness.settings import Settings


class DesignerError(RuntimeError):
    pass


@dataclass(slots=True)
class CommandResult:
    command: list[str]
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    log: str = ""
    executed: bool = False

    @property
    def ok(self) -> bool:
        return self.executed and self.returncode == 0

    def combined_output(self) -> str:
        chunks = [chunk.strip() for chunk in (self.stdout, self.stderr, self.log) if chunk.strip()]
        return "\n".join(chunks)


class Designer:
    """Auditable wrapper around 1cv8 DESIGNER batch commands."""

    def __init__(self, settings: Settings, *, connection_override: str | None = None) -> None:
        self.settings = settings
        if settings.onec_exe is None:
            raise DesignerError("ONEC_EXE is not configured")
        self.exe = settings.onec_exe.expanduser()
        self.connection = settings.onec_ib_connection if connection_override is None else connection_override
        if not self.connection.strip():
            raise DesignerError("1C infobase connection is not configured")

    @staticmethod
    def _split_args(raw: str) -> list[str]:
        tokens = re.findall(r'"[^"]*"|\S+', raw)
        return [token[1:-1] if len(token) >= 2 and token[0] == token[-1] == '"' else token for token in tokens]

    @staticmethod
    def _extension_args(extension: str | None = None, *, all_extensions: bool = False) -> list[str]:
        if extension and all_extensions:
            raise DesignerError("extension and all_extensions are mutually exclusive")
        if extension:
            name = extension.strip()
            if not name:
                raise DesignerError("Extension name cannot be empty")
            return ["-Extension", name]
        return ["-AllExtensions"] if all_extensions else []

    def _base_command(self) -> list[str]:
        command = [str(self.exe), "DESIGNER"]
        command.extend(self._split_args(self.connection))
        if self.settings.onec_user:
            command.extend(["/N", self.settings.onec_user])
        if self.settings.onec_password:
            command.extend(["/P", self.settings.onec_password])
        command.extend(["/DisableStartupMessages", "/DisableStartupDialogs"])
        return command

    def _run(self, action: list[str], *, execute: bool) -> CommandResult:
        command = [*self._base_command(), *action]
        if not execute:
            return CommandResult(command=command, returncode=None, executed=False)
        if not self.exe.exists():
            raise DesignerError(f"1C executable not found: {self.exe}")

        with tempfile.TemporaryDirectory(prefix="onec-harness-") as temp_dir:
            log_path = Path(temp_dir) / "designer.log"
            command_with_log = [*command, "/Out", str(log_path)]
            try:
                result = subprocess.run(
                    command_with_log,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.settings.onec_command_timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise DesignerError(
                    f"1C Designer command timed out after {self.settings.onec_command_timeout_seconds:g}s"
                ) from exc
            log = log_path.read_text(encoding="utf-8-sig", errors="replace") if log_path.exists() else ""
            return CommandResult(
                command=command_with_log,
                returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                log=log,
                executed=True,
            )

    def dump_config(
        self,
        target: Path,
        *,
        execute: bool = False,
        extension: str | None = None,
        all_extensions: bool = False,
    ) -> CommandResult:
        target = target.expanduser().resolve()
        if execute:
            target.mkdir(parents=True, exist_ok=True)
        action = ["/DumpConfigToFiles", str(target), *self._extension_args(extension, all_extensions=all_extensions)]
        return self._run(action, execute=execute)

    def load_config(
        self,
        source: Path,
        *,
        execute: bool = False,
        update_db: bool = False,
        update_dump_info: bool = False,
        extension: str | None = None,
        all_extensions: bool = False,
    ) -> CommandResult:
        action = [
            "/LoadConfigFromFiles",
            str(source.expanduser().resolve()),
            *self._extension_args(extension, all_extensions=all_extensions),
        ]
        if update_dump_info:
            action.append("-updateConfigDumpInfo")
        if update_db:
            action.append("/UpdateDBCfg")
        return self._run(action, execute=execute)

    def dump_cfg(self, target: Path, *, execute: bool = False, extension: str | None = None) -> CommandResult:
        target = target.expanduser().resolve()
        if execute:
            target.parent.mkdir(parents=True, exist_ok=True)
        action = ["/DumpCfg", str(target), *self._extension_args(extension)]
        return self._run(action, execute=execute)

    def build_external_processor(
        self,
        source_xml: Path,
        target_epf: Path,
        *,
        execute: bool = False,
    ) -> CommandResult:
        source_xml = source_xml.expanduser().resolve()
        target_epf = target_epf.expanduser().resolve()
        if execute:
            if not source_xml.exists():
                raise DesignerError(f"External processor source XML not found: {source_xml}")
            target_epf.parent.mkdir(parents=True, exist_ok=True)
        return self._run(
            ["/LoadExternalDataProcessorOrReportFromFiles", str(source_xml), str(target_epf)],
            execute=execute,
        )

    def update_db(self, *, execute: bool = False) -> CommandResult:
        return self._run(["/UpdateDBCfg"], execute=execute)

    def dump_infobase(self, target: Path, *, execute: bool = False) -> CommandResult:
        target = target.expanduser().resolve()
        if execute:
            target.parent.mkdir(parents=True, exist_ok=True)
        return self._run(["/DumpIB", str(target)], execute=execute)

    def check_modules(
        self,
        *,
        execute: bool = False,
        thin_client: bool = True,
        server: bool = True,
        external_connection: bool = True,
        extended: bool = True,
        extension: str | None = None,
        all_extensions: bool = False,
    ) -> CommandResult:
        action = ["/CheckModules", *self._extension_args(extension, all_extensions=all_extensions)]
        if thin_client:
            action.append("-ThinClient")
        if server:
            action.append("-Server")
        if external_connection:
            action.append("-ExternalConnection")
        if extended:
            action.append("-ExtendedModulesCheck")
        return self._run(action, execute=execute)

    def check_config(
        self,
        *,
        execute: bool = False,
        check_integrity: bool = True,
        incorrect_references: bool = True,
        handlers: bool = True,
        unreferenced_procedures: bool = True,
        extension: str | None = None,
        all_extensions: bool = False,
    ) -> CommandResult:
        action = ["/CheckConfig", *self._extension_args(extension, all_extensions=all_extensions)]
        if check_integrity:
            action.append("-ConfigLogIntegrity")
        if incorrect_references:
            action.append("-IncorrectReferences")
        if handlers:
            action.append("-HandlersExistence")
        if unreferenced_procedures:
            action.append("-UnreferenceProcedures")
        return self._run(action, execute=execute)

    def check_extension_applicability(self, extension: str, *, execute: bool = False) -> CommandResult:
        action = ["/CheckCanApplyConfigurationExtensions", *self._extension_args(extension)]
        return self._run(action, execute=execute)
