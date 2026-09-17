from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from onec_harness.metadata import ConfigurationIndex
from onec_harness.onec.designer import Designer, DesignerError
from onec_harness.providers.base import LLMProvider, Message
from onec_harness.snapshots import SnapshotError, SnapshotStore
from onec_harness.workspace import Workspace, WorkspaceError


SYSTEM_PROMPT = """Ты автономный инженер по 1С:Предприятие/BSL, работающий через безопасный harness.
Твоя задача — исследовать выгруженную конфигурацию и выполнить запрос пользователя минимальным точечным изменением.
Не выдумывай имена объектов, модулей, процедур или полей: сначала найди их через metadata/symbols/search/read.

На каждом шаге отвечай ТОЛЬКО одним JSON-объектом без Markdown.
Доступные действия:

{"tool":"metadata","args":{"query":"Заказ"}}
{"tool":"symbols","args":{"query":"Проведение"}}
{"tool":"search","args":{"query":"строка"}}
{"tool":"read","args":{"path":"relative/path.bsl"}}
{"tool":"patch","args":{"path":"relative/path.bsl","old":"точный старый фрагмент","new":"новый фрагмент"}}
{"tool":"diff","args":{}}
{"tool":"check_modules","args":{}}
{"tool":"check_config","args":{}}
{"tool":"rollback","args":{"snapshot_id":"id из результата patch"}}
{"tool":"finish","args":{"summary":"что выяснено/изменено и что проверить дальше"}}

Правила:
- patch должен быть минимальным; old должен встречаться ровно один раз;
- перед каждым patch harness автоматически делает snapshot затрагиваемого файла;
- никогда не используй абсолютные пути и ../;
- после изменения обязательно вызови diff;
- если проверки 1С разрешены, после изменения обязательно вызови check_modules;
- если check_modules/check_config вернул ошибку, изучи лог, исправь проблему и запусти проверку снова;
- не утверждай, что проверка 1С прошла, если harness не дал успешного результата;
- если данных недостаточно, исследуй проект дополнительными metadata/symbols/search/read;
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
    snapshots: list[str] = field(default_factory=list)
    checks_ok: bool | None = None


class AgentProtocolError(RuntimeError):
    pass


class HarnessAgent:
    def __init__(
        self,
        provider: LLMProvider,
        workspace: Workspace,
        *,
        designer: Designer | None = None,
        allow_writes: bool = False,
        execute_checks: bool = False,
        max_result_chars: int = 60_000,
    ) -> None:
        self.provider = provider
        self.workspace = workspace
        self.designer = designer
        self.allow_writes = allow_writes
        self.execute_checks = execute_checks
        self.max_result_chars = max_result_chars
        self.snapshots = SnapshotStore(workspace)
        self.index = ConfigurationIndex.build(workspace.root)
        self._patches_made = False
        self._diff_seen = False
        self._check_attempted = False
        self._last_check_ok: bool | None = None
        self._snapshot_ids: list[str] = []

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

    @staticmethod
    def _format_check(name: str, ok: bool, output: str) -> str:
        status = "OK" if ok else "FAILED"
        return f"{name}: {status}\n{output}".rstrip()

    def _execute_tool(self, tool: str, args: dict[str, Any]) -> str:
        if tool == "metadata":
            query = str(args.get("query", ""))
            return self._clip(self.index.describe_objects(query=query))

        if tool == "symbols":
            query = str(args.get("query", ""))
            return self._clip(self.index.describe_symbols(query=query))

        if tool == "search":
            query = str(args.get("query", ""))
            matches = self.workspace.search(query)[:100]
            if not matches:
                return "No matches"
            return self._clip("\n".join(f"{m.path}:{m.line}: {m.text}" for m in matches))

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
            snapshot = self.snapshots.create([path])
            self.workspace.replace_once(path, old, new)
            self._snapshot_ids.append(snapshot.snapshot_id)
            self._patches_made = True
            self._diff_seen = False
            self._check_attempted = False
            self._last_check_ok = None
            self.index = ConfigurationIndex.build(self.workspace.root)
            return f"Patched {path}. snapshot_id={snapshot.snapshot_id}"

        if tool == "diff":
            self._diff_seen = True
            return self._clip(self.workspace.git_diff() or "No changes")

        if tool in {"check_modules", "check_config"}:
            if not self.execute_checks:
                return "ERROR: 1C checks are disabled. Run agent with --check."
            if self.designer is None:
                return "ERROR: Designer is not configured"
            if tool == "check_modules":
                result = self.designer.check_modules(execute=True)
                label = "CheckModules"
            else:
                result = self.designer.check_config(execute=True)
                label = "CheckConfig"
            self._check_attempted = True
            self._last_check_ok = result.ok
            output = result.combined_output() or f"1C Designer exited with code {result.returncode}"
            return self._clip(self._format_check(label, result.ok, output))

        if tool == "rollback":
            snapshot_id = str(args.get("snapshot_id", ""))
            if not snapshot_id:
                return "ERROR: snapshot_id is required"
            restored = self.snapshots.restore(snapshot_id)
            self.index = ConfigurationIndex.build(self.workspace.root)
            self._patches_made = False
            self._diff_seen = False
            self._check_attempted = False
            self._last_check_ok = None
            return f"Restored snapshot {restored.snapshot_id}: {', '.join(restored.paths)}"

        return f"ERROR: unknown tool {tool!r}"

    def _finish_block_reason(self) -> str | None:
        if not self._patches_made:
            return None
        if not self._diff_seen:
            return "You changed files but have not inspected diff yet. Call diff before finish."
        if self.execute_checks and not self._check_attempted:
            return "You changed files but have not run check_modules yet. Call check_modules before finish."
        if self.execute_checks and self._last_check_ok is False:
            return "The latest 1C check failed. Fix the error or rollback before finish."
        return None

    async def run(self, task: str, *, max_steps: int = 16) -> AgentResult:
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
                    Message(role="user", content=f"PROTOCOL_ERROR: {exc}. Верни только корректный JSON action.")
                )
                continue

            tool = action["tool"]
            args = action["args"]
            if tool == "finish":
                blocked = self._finish_block_reason()
                if blocked:
                    messages.append(Message(role="assistant", content=response.content))
                    messages.append(Message(role="user", content=f"FINISH_BLOCKED: {blocked}"))
                    continue
                summary = str(args.get("summary", "")).strip() or "Agent finished without a summary."
                return AgentResult(
                    summary=summary,
                    steps=steps,
                    snapshots=list(self._snapshot_ids),
                    checks_ok=self._last_check_ok,
                )

            try:
                result = self._execute_tool(tool, args)
            except (WorkspaceError, SnapshotError, DesignerError, OSError) as exc:
                result = f"ERROR: {exc}"
            steps.append(AgentStep(tool=tool, args=args, result=result))
            messages.append(Message(role="assistant", content=response.content))
            messages.append(Message(role="user", content=f"TOOL_RESULT {tool}:\n{result}"))

        return AgentResult(
            summary=f"Stopped after reaching max_steps={max_steps}. No finish action received.",
            steps=steps,
            snapshots=list(self._snapshot_ids),
            checks_ok=self._last_check_ok,
        )
