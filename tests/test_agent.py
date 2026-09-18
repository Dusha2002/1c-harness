import asyncio
import subprocess
from pathlib import Path

from onec_harness.agent import HarnessAgent
from onec_harness.onec.designer import CommandResult
from onec_harness.providers.base import LLMResponse, Message
from onec_harness.skills import SkillStore
from onec_harness.workspace import Workspace


class FakeProvider:
    def __init__(self, responses: list[str]) -> None:
        self.responses = iter(responses)
        self.messages: list[list[Message]] = []

    async def complete(self, messages: list[Message]) -> LLMResponse:
        self.messages.append(list(messages))
        return LLMResponse(content=next(self.responses))


class FakeDesigner:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.stage_calls = 0
        self.module_calls = 0
        self.config_calls = 0

    def load_config(
        self,
        source: Path,
        *,
        execute: bool = False,
        update_db: bool = False,
        update_dump_info: bool = False,
    ) -> CommandResult:
        self.stage_calls += 1
        assert update_dump_info is True
        return CommandResult(
            command=["1cv8", "DESIGNER", "/LoadConfigFromFiles", str(source)],
            returncode=0 if self.ok else 1,
            log="staged" if self.ok else "load error",
            executed=execute,
        )

    def check_modules(self, *, execute: bool = False) -> CommandResult:
        self.module_calls += 1
        return CommandResult(
            command=["1cv8", "DESIGNER", "/CheckModules"],
            returncode=0 if self.ok else 1,
            log="modules ok" if self.ok else "syntax error",
            executed=execute,
        )

    def check_config(self, *, execute: bool = False) -> CommandResult:
        self.config_calls += 1
        return CommandResult(
            command=["1cv8", "DESIGNER", "/CheckConfig"],
            returncode=0 if self.ok else 1,
            log="config ok" if self.ok else "config error",
            executed=execute,
        )


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)


def test_agent_can_patch_and_requires_diff_before_finish(tmp_path: Path) -> None:
    _git_init(tmp_path)
    workspace = Workspace(tmp_path)
    workspace.write_text("Module.bsl", "old")
    subprocess.run(["git", "add", "Module.bsl"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=tmp_path, check=True, capture_output=True)
    provider = FakeProvider(
        [
            '{"tool":"patch","args":{"path":"Module.bsl","old":"old","new":"new"}}',
            '{"tool":"finish","args":{"summary":"too early"}}',
            '{"tool":"diff","args":{}}',
            '{"tool":"finish","args":{"summary":"done"}}',
        ]
    )
    agent = HarnessAgent(provider, workspace, allow_writes=True)

    result = asyncio.run(agent.run("change it"))

    assert result.summary == "done"
    assert workspace.read_text("Module.bsl") == "new"
    assert result.snapshots
    assert [step.tool for step in result.steps] == ["patch", "diff"]


def test_agent_refuses_patch_in_read_only_mode(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    workspace.write_text("Module.bsl", "old")
    provider = FakeProvider(
        [
            '{"tool":"patch","args":{"path":"Module.bsl","old":"old","new":"new"}}',
            '{"tool":"finish","args":{"summary":"blocked"}}',
        ]
    )
    agent = HarnessAgent(provider, workspace, allow_writes=False)

    result = asyncio.run(agent.run("change it"))

    assert result.summary == "blocked"
    assert workspace.read_text("Module.bsl") == "old"
    assert "source writes are disabled" in result.steps[0].result


def test_agent_requires_stage_and_both_checks_when_enabled(tmp_path: Path) -> None:
    _git_init(tmp_path)
    workspace = Workspace(tmp_path)
    workspace.write_text("Module.bsl", "old")
    subprocess.run(["git", "add", "Module.bsl"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=tmp_path, check=True, capture_output=True)
    provider = FakeProvider(
        [
            '{"tool":"patch","args":{"path":"Module.bsl","old":"old","new":"new"}}',
            '{"tool":"diff","args":{}}',
            '{"tool":"finish","args":{"summary":"too early"}}',
            '{"tool":"check_modules","args":{}}',
            '{"tool":"stage_config","args":{}}',
            '{"tool":"check_modules","args":{}}',
            '{"tool":"finish","args":{"summary":"still early"}}',
            '{"tool":"check_config","args":{}}',
            '{"tool":"finish","args":{"summary":"checked"}}',
        ]
    )
    designer = FakeDesigner(ok=True)
    agent = HarnessAgent(
        provider,
        workspace,
        designer=designer,  # type: ignore[arg-type]
        allow_writes=True,
        execute_checks=True,
    )

    result = asyncio.run(agent.run("change it"))

    assert result.summary == "checked"
    assert result.checks_ok is True
    assert designer.stage_calls == 1
    assert designer.module_calls == 1
    assert designer.config_calls == 1
    assert "stage_config first" in result.steps[2].result


def test_agent_skill_catalog_is_lazy_and_load_skill_adds_body(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    workspace.ensure_exists()
    provider = FakeProvider([
        '{"tool":"load_skill","args":{"name":"onec-engineering"}}',
        '{"tool":"finish","args":{"summary":"skill loaded"}}',
    ])
    agent = HarnessAgent(provider, workspace, skills=SkillStore(tmp_path / "skills"))

    result = asyncio.run(agent.run("Изучи конфигурацию 1С"))

    assert result.summary == "skill loaded"
    first_system = provider.messages[0][0].content
    assert "skill://onec-engineering" in first_system
    assert "Избегай запросов и циклов N+1" not in first_system
    second_call = provider.messages[1]
    tool_result = next(message.content for message in second_call if message.content.startswith("TOOL_RESULT load_skill:"))
    assert "SKILL_LOADED skill://onec-engineering" in tool_result
    assert "Избегай запросов и циклов N+1" in tool_result


def test_agent_does_not_reload_same_skill_body(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path / "workspace")
    workspace.ensure_exists()
    provider = FakeProvider([
        '{"tool":"load_skill","args":{"name":"highload-systems"}}',
        '{"tool":"load_skill","args":{"name":"highload-systems"}}',
        '{"tool":"finish","args":{"summary":"done"}}',
    ])
    agent = HarnessAgent(provider, workspace, skills=SkillStore(tmp_path / "skills"))

    result = asyncio.run(agent.run("Проанализируй нагрузку"))

    assert result.steps[0].result.startswith("SKILL_LOADED")
    assert result.steps[1].result == "SKILL_ALREADY_LOADED skill://highload-systems"
