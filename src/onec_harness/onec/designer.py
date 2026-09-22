from __future__ import annotations

import re
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from onec_harness.settings import Settings
from onec_harness.connections import require_test_connection


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

    def __init__(
        self,
        settings: Settings,
        *,
        connection_override: str | None = None,
        auth_kind: str = "primary",
    ) -> None:
        self.settings = settings
        if settings.onec_exe is None:
            raise DesignerError("ONEC_EXE is not configured")
        if connection_override is not None:
            try:
                require_test_connection(settings.onec_ib_connection, connection_override)
            except ValueError as exc:
                raise DesignerError(str(exc)) from exc
        self.exe = settings.onec_exe.expanduser()
        self.connection = settings.onec_ib_connection if connection_override is None else connection_override
        if auth_kind == "primary":
            self.user = settings.onec_user
            self.password = settings.onec_password
        elif auth_kind == "staging":
            self.user = settings.onec_staging_user
            self.password = settings.onec_staging_password
        elif auth_kind == "none":
            self.user = None
            self.password = None
        else:
            raise DesignerError(f"Unknown 1C authentication kind: {auth_kind}")
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
        if self.user:
            command.extend(["/N", self.user])
        if self.password:
            command.extend(["/P", self.password])
        command.extend(["/DisableStartupMessages", "/DisableStartupDialogs"])
        return command

    @staticmethod
    def _stop_process(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    def _run_monitored(
        self,
        action: list[str],
        *,
        execute: bool,
        progress: Callable[[float], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
        timeout_seconds: float | None = None,
    ) -> CommandResult:
        command = [*self._base_command(), *action]
        if not execute:
            return CommandResult(command=command, returncode=None, executed=False)
        if not self.exe.exists():
            raise DesignerError(f"1C executable not found: {self.exe}")

        timeout = timeout_seconds or self.settings.onec_command_timeout_seconds
        fatal_markers = (
            "пользователь иб не идентифицирован",
            "неверное имя или пароль",
            "неверный пароль",
        )
        with tempfile.TemporaryDirectory(prefix="onec-harness-") as temp_dir:
            root = Path(temp_dir)
            log_path = root / "designer.log"
            stdout_path = root / "stdout.log"
            stderr_path = root / "stderr.log"
            command_with_log = [*command, "/Out", str(log_path)]
            started = time.monotonic()

            with stdout_path.open("wb") as stdout_stream, stderr_path.open("wb") as stderr_stream:
                process = subprocess.Popen(
                    command_with_log,
                    stdout=stdout_stream,
                    stderr=stderr_stream,
                )
                try:
                    next_progress = 0.0
                    while process.poll() is None:
                        elapsed = time.monotonic() - started
                        if cancelled and cancelled():
                            self._stop_process(process)
                            raise DesignerError("Операция 1С отменена пользователем")
                        if elapsed >= timeout:
                            self._stop_process(process)
                            raise DesignerError(f"1C Designer command timed out after {timeout:g}s")

                        log = (
                            log_path.read_text(encoding="utf-8-sig", errors="replace")
                            if log_path.exists()
                            else ""
                        )
                        if any(marker in log.casefold() for marker in fatal_markers):
                            self._stop_process(process)
                            break

                        if progress and elapsed >= next_progress:
                            progress(elapsed)
                            next_progress = elapsed + 1.0
                        time.sleep(0.2)
                finally:
                    if process.poll() is None:
                        self._stop_process(process)

            if progress:
                progress(time.monotonic() - started)
            stdout = stdout_path.read_bytes().decode("utf-8", errors="replace") if stdout_path.exists() else ""
            stderr = stderr_path.read_bytes().decode("utf-8", errors="replace") if stderr_path.exists() else ""
            log = log_path.read_text(encoding="utf-8-sig", errors="replace") if log_path.exists() else ""
            return CommandResult(
                command=command_with_log,
                returncode=process.returncode,
                stdout=stdout,
                stderr=stderr,
                log=log,
                executed=True,
            )

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
        progress: Callable[[float], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
        timeout_seconds: float | None = None,
    ) -> CommandResult:
        target = target.expanduser().resolve()
        if execute:
            target.mkdir(parents=True, exist_ok=True)
        action = ["/DumpConfigToFiles", str(target), *self._extension_args(extension, all_extensions=all_extensions)]
        if progress is not None or cancelled is not None or timeout_seconds is not None:
            return self._run_monitored(
                action,
                execute=execute,
                progress=progress,
                cancelled=cancelled,
                timeout_seconds=timeout_seconds,
            )
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

    def create_file_infobase(self, target: Path, *, execute: bool = False) -> CommandResult:
        """Create an empty file infobase without touching the primary connection."""
        target = target.expanduser().resolve()
        command = [
            str(self.exe),
            "CREATEINFOBASE",
            f'File="{target}"',
            "/DisableStartupMessages",
            "/DisableStartupDialogs",
        ]
        if not execute:
            return CommandResult(command=command, returncode=None, executed=False)
        if not self.exe.exists():
            raise DesignerError(f"1C executable not found: {self.exe}")
        target.mkdir(parents=True, exist_ok=True)
        if (target / "1Cv8.1CD").exists():
            raise DesignerError(f"Infobase already exists: {target}")

        with tempfile.TemporaryDirectory(prefix="onec-harness-create-") as temp_dir:
            log_path = Path(temp_dir) / "create-infobase.log"
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
                    f"1C infobase creation timed out after {self.settings.onec_command_timeout_seconds:g}s"
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
