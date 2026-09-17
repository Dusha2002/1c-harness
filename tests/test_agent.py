import asyncio
from pathlib import Path

from onec_harness.agent import HarnessAgent
from onec_harness.providers.base import LLMResponse, Message
from onec_harness.workspace import Workspace


class FakeProvider:
    def __init__(self, responses: list[str]) -> None:
        self.responses = iter(responses)
        self.messages: list[list[Message]] = []

    async def complete(self, messages: list[Message]) -> LLMResponse:
        self.messages.append(list(messages))
        return LLMResponse(content=next(self.responses))


def test_agent_can_patch_when_write_enabled(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    workspace.write_text("Module.bsl", "old")
    provider = FakeProvider(
        [
            '{"tool":"patch","args":{"path":"Module.bsl","old":"old","new":"new"}}',
            '{"tool":"finish","args":{"summary":"done"}}',
        ]
    )
    agent = HarnessAgent(provider, workspace, allow_writes=True)

    result = asyncio.run(agent.run("change it"))

    assert result.summary == "done"
    assert workspace.read_text("Module.bsl") == "new"


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
    assert "writes are disabled" in result.steps[0].result
