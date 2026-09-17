from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from onec_harness.providers.base import LLMProvider, Message
from onec_harness.workspace import Workspace, WorkspaceError


SYSTEM_PROMPT = """Ты автономный инженер по 1С:Предприятие/BSL, работающий через безопасный harness.
Твоя задача — исследовать выгруженную конфигурацию и выполнить запрос пользователя минимальным точечным изменением.
Не выдумывай имена объектов, модулей, процедур или полей: сначала найди их через search/read.

На каждом шаге отвечай ТОЛЬКО одним JSON-объектом без Markdown.
Доступные действия:

{"tool":"search","args":{"query":"строка"}}
{"tool":"read","args":{"path":"relative/path.bsl"}}
{"tool":"patch","args":{"path":"relative/path.bsl","old":"точный старый фрагмент","new":"новый фрагмент"}}
{"tool":"diff","args":{}}
{"tool":"finish","args":{"summary":"что выяснено/изменено и что проверить дальше"}}

Правила:
- patch должен быть минимальным; old должен встречаться ровно один раз;
- никогда не используй абсолютные пути и ../;
- после изменения обязательно вызови diff;
- не утверждай, что проверка 1С прошла, если harness не дал такого результата;
- если данных недостаточно, исследуй проект дополнительными search/read;
- finish используй только когда задача выполнена или конкретно объяснена блокировка.
"""


@dataclass(slots=True)
class AgentStep:
    tool: str
    args: dict[str, Any]
    result: str


@dataclass(slots=True)
class AgentResult:
    summary: str
    steps: list[AgentStep] = field(default_factory=list)


class AgentProtocolError(RuntimeError):
    pass


class HarnessAgent:
    def __init__(
        self,
        provider: LLMProvider,
        workspace: Workspace,
        *,
        allow_writes: bool = False,
        max_result_chars: int = 60_000,
    ) -> None:
        self.provider = provider
        self.workspace = workspace
        self.allow_writes = allow_writes
        self.max_result_chars = max_result_chars

    @staticmethod
    def _parse_action(text: str) -> dict[str, Any]:
        candidate = text.strip()
        if candidate.startswith("```"):
            lines = candidate.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            candidate = "\n".join(lines).strip()
        try:
            action = json.loads(candidate)
        except json.JSONDecodeError:
            start = candidate.find("{")
            end = candidate.rfind("}")
            if start < 0 or end <= start:
                raise AgentProtocolError("Model did not return a JSON action") from None
            try:
                action = json.loads(candidate[start : end + 1])
            except json.JSONDecodeError as exc:
                raise AgentProtocolError("Model returned invalid JSON action") from exc
        if not isinstance(action, dict) or not isinstance(action.get("tool"), str):
            raise AgentProtocolError("Action must contain string field 'tool'")
        args = action.get("args", {})
        if not isinstance(args, dict):
            raise AgentProtocolError("Action field 'args' must be an object")
        action["args"] = args
        return action

    def _clip(self, value: str) -> str:
        if len(value) <= self.max_result_chars:
            return value
        return value[: self.max_result_chars] + "\n...[truncated by harness]"

    def _execute_tool(self, tool: str, args: dict[str, Any]) -> str:
        if tool == "search":
            query = str(args.get("query", ""))
            matches = self.workspace.search(query)[:100]
            if not matches:
                return "No matches"
            return self._clip(
                "\n".join(f"{m.path}:{m.line}: {m.text}" for m in matches)
            )

        if tool == "read":
            path = str(args.get("path", ""))
            if not path:
                return "ERROR: path is required"
            return self._clip(self.workspace.read_text(path))

        if tool == "patch":
            if not self.allow_writes:
                return "ERROR: writes are disabled. User must run agent with --write."
            path = str(args.get("path", ""))
            old = str(args.get("old", ""))
            new = str(args.get("new", ""))
            if not path or not old:
                return "ERROR: patch requires path and non-empty old text"
            self.workspace.replace_once(path, old, new)
            return f"Patched {path}"

        if tool == "diff":
            return self._clip(self.workspace.git_diff() or "No changes")

        return f"ERROR: unknown tool {tool!r}"

    async def run(self, task: str, *, max_steps: int = 12) -> AgentResult:
        if max_steps < 1:
            raise ValueError("max_steps must be >= 1")
        messages = [
            Message(role="system", content=SYSTEM_PROMPT),
            Message(role="user", content=f"Задача:\n{task}"),
        ]
        steps: list[AgentStep] = []

        for _ in range(max_steps):
            response = await self.provider.complete(messages)
            try:
                action = self._parse_action(response.content)
            except AgentProtocolError as exc:
                messages.append(Message(role="assistant", content=response.content))
                messages.append(
                    Message(
                        role="user",
                        content=f"PROTOCOL_ERROR: {exc}. Верни только корректный JSON action.",
                    )
                )
                continue

            tool = action["tool"]
            args = action["args"]
            if tool == "finish":
                summary = str(args.get("summary", "")).strip()
                if not summary:
                    summary = "Agent finished without a summary."
                return AgentResult(summary=summary, steps=steps)

            try:
                result = self._execute_tool(tool, args)
            except (WorkspaceError, OSError) as exc:
                result = f"ERROR: {exc}"
            steps.append(AgentStep(tool=tool, args=args, result=result))
            messages.append(Message(role="assistant", content=response.content))
            messages.append(
                Message(
                    role="user",
                    content=f"TOOL_RESULT {tool}:\n{result}",
                )
            )

        return AgentResult(
            summary=f"Stopped after reaching max_steps={max_steps}. No finish action received.",
            steps=steps,
        )
