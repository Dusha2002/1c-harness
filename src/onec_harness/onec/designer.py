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


class Designer:
    """Auditable wrapper around 1cv8 DESIGNER batch commands."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if settings.onec_exe is None:
            raise DesignerError("ONEC_EXE is not configured")
        self.exe = settings.onec_exe.expanduser()

    @staticmethod
    def _split_args(raw: str) -> list[str]:
        # Windows-oriented parser: tokens may be quoted, quotes are stripped.
        tokens = re.findall(r'"[^"]*"|\S+', raw)
        return [token[1:-1] if len(token) >= 2 and token[0] == token[-1] == '"' else token for token in tokens]

    def _base_command(self) -> list[str]:
        command = [str(self.exe), "DESIGNER"]
        command.extend(self._split_args(self.settings.onec_ib_connection))
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
                    f"1C Designer command timed out after "
                    f"{self.settings.onec_command_timeout_seconds:g}s"
                ) from exc
            log = ""
            if log_path.exists():
                log = log_path.read_text(encoding="utf-8-sig", errors="replace")
            return CommandResult(
                command=command_with_log,
                returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                log=log,
                executed=True,
            )

    def dump_config(self, target: Path, *, execute: bool = False) -> CommandResult:
        target = target.expanduser().resolve()
        if execute:
            target.mkdir(parents=True, exist_ok=True)
        return self._run(["/DumpConfigToFiles", str(target)], execute=execute)

    def load_config(
        self,
        source: Path,
        *,
        execute: bool = False,
        update_db: bool = False,
    ) -> CommandResult:
        action = ["/LoadConfigFromFiles", str(source.expanduser().resolve())]
        if update_db:
            action.append("/UpdateDBCfg")
        return self._run(action, execute=execute)

    def check_modules(
        self,
        *,
        execute: bool = False,
        thin_client: bool = True,
        server: bool = True,
        external_connection: bool = True,
        extended: bool = True,
    ) -> CommandResult:
        action = ["/CheckModules"]
        if thin_client:
            action.append("-ThinClient")
        if server:
            action.append("-Server")
        if external_connection:
            action.append("-ExternalConnection")
        if extended:
            action.append("-ExtendedModulesCheck")
        return self._run(action, execute=execute)
