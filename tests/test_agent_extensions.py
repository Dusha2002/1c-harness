import asyncio
import subprocess
from pathlib import Path

from onec_harness.agent import HarnessAgent
from onec_harness.onec.designer import CommandResult
from onec_harness.providers.base import LLMResponse, Message
from onec_harness.semantic import MetadataEditor
from onec_harness.workspace import Workspace


CONFIG = '''<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses" version="2.17">
  <Configuration uuid="00000000-0000-0000-0000-000000000001">
    <Properties><Name>HarnessTest</Name></Properties>
    <ChildObjects/>
  </Configuration>
</MetaDataObject>
'''

EXTENSION = '''<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses" version="2.17">
  <Configuration uuid="00000000-0000-0000-0000-000000000099">
    <Properties><ObjectBelonging>Adopted</ObjectBelonging><Name>HarnessExt</Name></Properties>
    <ChildObjects/>
  </Configuration>
</MetaDataObject>
'''


class FakeProvider:
    def __init__(self, responses: list[str]) -> None:
        self.responses = iter(responses)

    async def complete(self, messages: list[Message]) -> LLMResponse:
        return LLMResponse(content=next(self.responses))


class FakeDesigner:
    def __init__(self) -> None:
        self.extensions: list[str] = []

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
        assert update_dump_info is True
        assert extension == "HarnessExt"
        self.extensions.append(extension)
        return CommandResult(["1cv8", "/LoadConfigFromFiles", str(source)], 0, log="stage ok", executed=execute)

    def check_modules(
        self,
        *,
        execute: bool = False,
        extension: str | None = None,
        all_extensions: bool = False,
        **kwargs: object,
    ) -> CommandResult:
        assert extension == "HarnessExt"
        return CommandResult(["1cv8", "/CheckModules"], 0, log="modules ok", executed=execute)

    def check_config(
        self,
        *,
        execute: bool = False,
        extension: str | None = None,
        all_extensions: bool = False,
        **kwargs: object,
    ) -> CommandResult:
        assert extension == "HarnessExt"
        return CommandResult(["1cv8", "/CheckConfig"], 0, log="config ok", executed=execute)

    def check_extension_applicability(self, extension: str, *, execute: bool = False) -> CommandResult:
        return CommandResult(["1cv8", "/CheckCanApplyConfigurationExtensions"], 0, executed=execute)


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)


def test_extension_change_requires_its_own_stage_and_checks(tmp_path: Path) -> None:
    _git_init(tmp_path)
    workspace = Workspace(tmp_path)
    workspace.write_text("Configuration.xml", CONFIG)
    workspace.write_text("Extensions/HarnessExt/Configuration.xml", EXTENSION)
    MetadataEditor(workspace).create_document("Заказ")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=tmp_path, check=True, capture_output=True)

    provider = FakeProvider([
        '{"tool":"borrow_extension_object","args":{"extension":"HarnessExt","kind":"document","object_name":"Заказ"}}',
        '{"tool":"diff","args":{}}',
        '{"tool":"finish","args":{"summary":"too early"}}',
        '{"tool":"stage_extension","args":{"extension":"HarnessExt"}}',
        '{"tool":"check_extension_modules","args":{"extension":"HarnessExt"}}',
        '{"tool":"check_extension_config","args":{"extension":"HarnessExt"}}',
        '{"tool":"finish","args":{"summary":"extension checked"}}',
    ])
    designer = FakeDesigner()
    agent = HarnessAgent(
        provider,
        workspace,
        designer=designer,  # type: ignore[arg-type]
        allow_writes=True,
        execute_checks=True,
    )

    result = asyncio.run(agent.run("borrow document in extension"))

    assert result.summary == "extension checked"
    assert result.checks_ok is True
    assert designer.extensions == ["HarnessExt"]
    assert [step.tool for step in result.steps] == [
        "borrow_extension_object",
        "diff",
        "stage_extension",
        "check_extension_modules",
        "check_extension_config",
    ]
