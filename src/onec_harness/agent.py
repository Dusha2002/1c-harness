from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from onec_harness.metadata import ConfigurationIndex
from onec_harness.onec.com import ComConnector, ComConnectorError
from onec_harness.onec.designer import Designer, DesignerError
from onec_harness.onec.e2e import TestManagerRunner
from onec_harness.onec.testing import ScenarioCompiler, TestClientError
from onec_harness.providers.base import LLMProvider, Message
from onec_harness.semantic import SemanticMetadataError
from onec_harness.semantic_tools import SEMANTIC_TOOLS, SemanticToolExecutor
from onec_harness.snapshots import SnapshotError, SnapshotStore
from onec_harness.workspace import Workspace, WorkspaceError


SYSTEM_PROMPT = """Ты автономный инженер по 1С:Предприятие/BSL, работающий через безопасный harness.
Исследуй реальную конфигурацию и выполняй запрос минимальными, проверяемыми изменениями.
Не выдумывай имена объектов, модулей, процедур, реквизитов или полей: сначала используй metadata/symbols/search/read.

На каждом шаге отвечай ТОЛЬКО одним JSON-объектом без Markdown.
Основные действия:
{"tool":"metadata","args":{"query":"Заказ"}}
{"tool":"symbols","args":{"query":"Проведение"}}
{"tool":"search","args":{"query":"строка"}}
{"tool":"read","args":{"path":"relative/path.bsl"}}
{"tool":"patch","args":{"path":"relative/path.bsl","old":"точный старый фрагмент","new":"новый фрагмент"}}
{"tool":"create_catalog","args":{"name":"Оборудование","synonym":"Оборудование","hierarchical":false}}
{"tool":"create_document_meta","args":{"name":"Заявка","synonym":"Заявка","posting":false}}
{"tool":"create_enum","args":{"name":"Статусы","values":["Новый","Закрыт"]}}
{"tool":"add_enum_value","args":{"enum_name":"Статусы","name":"Отменен","synonym":"Отменен"}}
{"tool":"add_attribute","args":{"kind":"catalog","object_name":"Оборудование","name":"СерийныйНомер","value_type":"string","string_length":100}}
{"tool":"add_tabular_section","args":{"kind":"document","object_name":"Заявка","name":"Товары","columns":[{"name":"Товар","value_type":"CatalogRef.Товары"},{"name":"Количество","value_type":"number","digits":15,"fraction_digits":3}]}}
{"tool":"create_information_register","args":{"name":"Цены","periodicity":"Nonperiodical","write_mode":"Independent","dimensions":[{"name":"Товар","value_type":"CatalogRef.Товары","main_filter":true}],"resources":[{"name":"Цена","value_type":"number","digits":15,"fraction_digits":2}]}}
{"tool":"create_accumulation_register","args":{"name":"ОстаткиТоваров","register_type":"Balance","dimensions":[{"name":"Товар","value_type":"CatalogRef.Товары"}],"resources":[{"name":"Количество","value_type":"number","digits":15,"fraction_digits":3}]}}
{"tool":"ensure_module","args":{"kind":"catalog","object_name":"Оборудование","module":"object","content":""}}
{"tool":"diff","args":{}}
{"tool":"stage_config","args":{"update_db":false}}
{"tool":"check_modules","args":{}}
{"tool":"check_config","args":{}}
{"tool":"rollback","args":{"snapshot_id":"id из результата изменения"}}

Read-only runtime tools (через 1С, не SQL к СУБД):
{"tool":"runtime_query","args":{"text":"ВЫБРАТЬ ...","fields":["Поле"],"parameters":{},"limit":100}}
{"tool":"catalog_items","args":{"name":"Контрагенты","fields":["Ссылка","Код","Наименование"],"filters":{},"limit":50}}
{"tool":"document_items","args":{"name":"ЗаказПокупателя","fields":["Ссылка","Дата","Номер"],"filters":{},"limit":50}}
{"tool":"register_records","args":{"kind":"accumulation","name":"ТоварыНаСкладах","fields":["Товар","Количество"],"filters":{},"limit":50}}

Runtime write tools существуют только при явном разрешении пользователя:
{"tool":"create_catalog_item","args":{"name":"Оборудование","attributes":{"Наименование":"Тест"}}}
{"tool":"create_document_record","args":{"name":"Заявка","attributes":{},"post":false}}

UI testing:
{"tool":"ui_test_scenario","args":{"actions":[{"action":"execute_command","link":"e1cib/command/Catalog.Оборудование.Create"},{"action":"wait_form","title":"Оборудование*"}]}}
{"tool":"run_ui_test","args":{"actions":[{"action":"execute_command","link":"e1cib/command/Catalog.Оборудование.Create"},{"action":"wait_form","title":"Оборудование*"}]}}

Завершение:
{"tool":"finish","args":{"summary":"что сделано и как проверено"}}

Правила:
- Любое изменение исходников автоматически получает snapshot.
- patch должен быть минимальным, old должен встречаться ровно один раз.
- Для создания/изменения метаданных предпочитай semantic tools вместо ручного редактирования XML.
- Никогда не используй абсолютные пути и ../.
- После любого изменения исходников обязательно вызови diff.
- Если включена проверка 1С: после изменения вызови stage_config, затем check_modules И check_config.
- stage_config работает только с отдельной staging-инфобазой; не загружай изменения в основную базу.
- Если stage/check вернул ошибку, изучи лог, исправь исходники и повтори полный цикл stage/check.
- update_db=true допустим только для staging и обязателен перед E2E UI тестом изменённых метаданных/кода.
- Runtime-запись не выполняй без явного разрешения; read-only tools можно использовать для диагностики.
- ui_test_scenario только генерирует BSL. run_ui_test реально собирает EPF и запускает Test Client/Test Manager.
- Для задач, меняющих пользовательский UI/формы/интерактивное поведение, при доступном run_ui_test предпочитай фактический E2E тест.
- Если run_ui_test был запущен и упал, не завершай задачу как успешную: исправь или откати изменение.
- Не утверждай об успешной проверке, если harness не вернул OK.
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
    ui_test_ok: bool | None = None


class AgentProtocolError(RuntimeError):
    pass


class HarnessAgent:
    def __init__(
        self,
        provider: LLMProvider,
        workspace: Workspace,
        *,
        designer: Designer | None = None,
        runtime: ComConnector | None = None,
        scenario_compiler: ScenarioCompiler | None = None,
        test_runner: TestManagerRunner | None = None,
        allow_writes: bool = False,
        execute_checks: bool = False,
        execute_ui_tests: bool = False,
        allow_runtime_writes: bool = False,
        max_result_chars: int = 60_000,
    ) -> None:
        self.provider = provider
        self.workspace = workspace
        self.designer = designer
        self.runtime = runtime
        self.scenario_compiler = scenario_compiler
        self.test_runner = test_runner
        self.allow_writes = allow_writes
        self.execute_checks = execute_checks
        self.execute_ui_tests = execute_ui_tests
        self.allow_runtime_writes = allow_runtime_writes
        self.max_result_chars = max_result_chars
        self.snapshots = SnapshotStore(workspace)
        self.semantic_tools = SemanticToolExecutor(workspace)
        self.index = ConfigurationIndex.build(workspace.root)
        self._source_changed = False
        self._diff_seen = False
        self._stage_attempted = False
        self._stage_ok: bool | None = None
        self._stage_db_updated = False
        self._module_check_ok: bool | None = None
        self._config_check_ok: bool | None = None
        self._ui_test_attempted = False
        self._ui_test_ok: bool | None = None
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
        return f"{name}: {'OK' if ok else 'FAILED'}\n{output}".rstrip()

    @staticmethod
    def _string_list(value: Any, *, label: str) -> list[str]:
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise AgentProtocolError(f"{label} must be an array of strings")
        return value

    @staticmethod
    def _mapping(value: Any, *, label: str) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise AgentProtocolError(f"{label} must be an object")
        return value

    @staticmethod
    def _action_list(value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise AgentProtocolError("actions must be an array of objects")
        return value

    def _reset_validation(self) -> None:
        self._diff_seen = False
        self._stage_attempted = False
        self._stage_ok = None
        self._stage_db_updated = False
        self._module_check_ok = None
        self._config_check_ok = None
        self._ui_test_attempted = False
        self._ui_test_ok = None

    def _mark_source_change(self, snapshot_id: str) -> None:
        self._source_changed = True
        self._snapshot_ids.append(snapshot_id)
        self._reset_validation()
        self.index = ConfigurationIndex.build(self.workspace.root)

    def _semantic_result(self, change: Any) -> str:
        self._mark_source_change(change.snapshot_id)
        return f"{change.summary}. snapshot_id={change.snapshot_id}; paths={', '.join(change.paths)}"

    def _require_runtime(self) -> ComConnector:
        if self.runtime is None:
            raise ComConnectorError("COM runtime adapter is not configured")
        return self.runtime

    def _execute_tool(self, tool: str, args: dict[str, Any]) -> str:
        if tool == "metadata":
            return self._clip(self.index.describe_objects(query=str(args.get("query", ""))))
        if tool == "symbols":
            return self._clip(self.index.describe_symbols(query=str(args.get("query", ""))))
        if tool == "search":
            matches = self.workspace.search(str(args.get("query", "")))[:100]
            return self._clip("\n".join(f"{m.path}:{m.line}: {m.text}" for m in matches) or "No matches")
        if tool == "read":
            path = str(args.get("path", ""))
            if not path:
                return "ERROR: path is required"
            return self._clip(self.workspace.read_text(path))

        if tool == "patch":
            if not self.allow_writes:
                return "ERROR: source writes are disabled. Run agent with --write."
            path = str(args.get("path", ""))
            old = str(args.get("old", ""))
            new = str(args.get("new", ""))
            if not path or not old:
                return "ERROR: patch requires path and non-empty old text"
            snapshot = self.snapshots.create([path])
            self.workspace.replace_once(path, old, new)
            self._mark_source_change(snapshot.snapshot_id)
            return f"Patched {path}. snapshot_id={snapshot.snapshot_id}"

        if tool in SEMANTIC_TOOLS:
            if not self.allow_writes:
                return "ERROR: source writes are disabled. Run agent with --write."
            return self._semantic_result(self.semantic_tools.execute(tool, args))

        if tool == "diff":
            self._diff_seen = True
            return self._clip(self.workspace.git_diff() or "No changes")

        if tool == "stage_config":
            if not self.execute_checks:
                return "ERROR: staging is disabled. Run agent with --check and configure staging DB."
            if self.designer is None:
                return "ERROR: staging Designer is not configured"
            update_db = bool(args.get("update_db", False))
            result = self.designer.load_config(
                self.workspace.root,
                execute=True,
                update_db=update_db,
                update_dump_info=True,
            )
            self._stage_attempted = True
            self._stage_ok = result.ok
            self._stage_db_updated = bool(result.ok and update_db)
            self._module_check_ok = None
            self._config_check_ok = None
            self._ui_test_attempted = False
            self._ui_test_ok = None
            output = result.combined_output() or f"1C Designer exited with code {result.returncode}"
            return self._clip(self._format_check("StageConfig", result.ok, output))

        if tool in {"check_modules", "check_config"}:
            if not self.execute_checks:
                return "ERROR: 1C checks are disabled. Run agent with --check."
            if self.designer is None:
                return "ERROR: staging Designer is not configured"
            if self._source_changed and self._stage_ok is not True:
                return "ERROR: changed workspace has not been successfully loaded into staging. Call stage_config first."
            if tool == "check_modules":
                result = self.designer.check_modules(execute=True)
                self._module_check_ok = result.ok
                label = "CheckModules"
            else:
                result = self.designer.check_config(execute=True)
                self._config_check_ok = result.ok
                label = "CheckConfig"
            output = result.combined_output() or f"1C Designer exited with code {result.returncode}"
            return self._clip(self._format_check(label, result.ok, output))

        if tool == "rollback":
            snapshot_id = str(args.get("snapshot_id", ""))
            if not snapshot_id:
                return "ERROR: snapshot_id is required"
            restored = self.snapshots.restore(snapshot_id)
            self.index = ConfigurationIndex.build(self.workspace.root)
            self._source_changed = bool(self.workspace.git_diff().strip())
            self._reset_validation()
            return f"Restored snapshot {restored.snapshot_id}: {', '.join(restored.paths)}"

        if tool == "runtime_query":
            runtime = self._require_runtime()
            rows = runtime.query(
                str(args.get("text", "")),
                fields=self._string_list(args.get("fields"), label="fields"),
                parameters=self._mapping(args.get("parameters"), label="parameters"),
                limit=int(args.get("limit", 200)),
            )
            return self._clip(json.dumps(rows, ensure_ascii=False, default=str))

        if tool in {"catalog_items", "document_items"}:
            runtime = self._require_runtime()
            fields = self._string_list(args.get("fields"), label="fields")
            common = {
                "name": str(args.get("name", "")),
                "fields": fields,
                "filters": self._mapping(args.get("filters"), label="filters"),
                "limit": int(args.get("limit", 100)),
            }
            rows = runtime.catalog_items(**common) if tool == "catalog_items" else runtime.document_items(**common)
            return self._clip(json.dumps(rows, ensure_ascii=False, default=str))

        if tool == "register_records":
            rows = self._require_runtime().register_records(
                str(args.get("kind", "")),
                str(args.get("name", "")),
                fields=self._string_list(args.get("fields"), label="fields"),
                filters=self._mapping(args.get("filters"), label="filters"),
                limit=int(args.get("limit", 100)),
            )
            return self._clip(json.dumps(rows, ensure_ascii=False, default=str))

        if tool in {"create_catalog_item", "create_document_record"}:
            if not self.allow_runtime_writes:
                return "ERROR: runtime writes are disabled. They require explicit --runtime-write and environment opt-in."
            runtime = self._require_runtime()
            attributes = self._mapping(args.get("attributes"), label="attributes")
            if tool == "create_catalog_item":
                ref = runtime.create_catalog_item(str(args.get("name", "")), attributes)
            else:
                ref = runtime.create_document(
                    str(args.get("name", "")),
                    attributes,
                    post=bool(args.get("post", False)),
                )
            return f"Runtime object created: {ref}"

        if tool == "ui_test_scenario":
            if self.scenario_compiler is None:
                return "ERROR: UI test compiler is not configured"
            return self._clip(self.scenario_compiler.compile(self._action_list(args.get("actions"))))

        if tool == "run_ui_test":
            if not self.execute_ui_tests:
                return "ERROR: E2E UI test execution is disabled. Run agent with --ui-test."
            if self.test_runner is None:
                return "ERROR: Test Manager E2E runner is not configured"
            if self._source_changed and self._stage_db_updated is not True:
                return "ERROR: changed sources require successful stage_config with update_db=true before E2E UI testing."
            result = self.test_runner.run(self._action_list(args.get("actions")), execute=True)
            self._ui_test_attempted = True
            self._ui_test_ok = result.success is True
            return self._clip(json.dumps(result.as_dict(), ensure_ascii=False, default=str))

        return f"ERROR: unknown tool {tool!r}"

    def _checks_ok(self) -> bool | None:
        if not self.execute_checks or not self._source_changed:
            return None
        return bool(self._stage_ok and self._module_check_ok and self._config_check_ok)

    def _finish_block_reason(self) -> str | None:
        if self._source_changed:
            if not self._diff_seen:
                return "You changed files but have not inspected diff yet. Call diff before finish."
            if self.execute_checks:
                if not self._stage_attempted or self._stage_ok is not True:
                    return "Changed files are not successfully loaded into staging. Call stage_config and fix any error."
                if self._module_check_ok is not True:
                    return "Run check_modules successfully against staging before finish."
                if self._config_check_ok is not True:
                    return "Run check_config successfully against staging before finish."
        if self._ui_test_attempted and self._ui_test_ok is not True:
            return "The latest E2E UI test failed. Fix the problem or rollback before finish."
        return None

    async def run(self, task: str, *, max_steps: int = 24) -> AgentResult:
        if max_steps < 1:
            raise ValueError("max_steps must be >= 1")
        messages = [Message(role="system", content=SYSTEM_PROMPT), Message(role="user", content=f"Задача:\n{task}")]
        steps: list[AgentStep] = []

        for _ in range(max_steps):
            response = await self.provider.complete(messages)
            try:
                action = self._parse_action(response.content)
            except AgentProtocolError as exc:
                messages.append(Message(role="assistant", content=response.content))
                messages.append(Message(role="user", content=f"PROTOCOL_ERROR: {exc}. Верни только корректный JSON action."))
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
                return AgentResult(summary, steps, list(self._snapshot_ids), self._checks_ok(), self._ui_test_ok)

            try:
                result = self._execute_tool(tool, args)
            except (
                WorkspaceError,
                SnapshotError,
                DesignerError,
                SemanticMetadataError,
                ComConnectorError,
                TestClientError,
                AgentProtocolError,
                OSError,
                ValueError,
            ) as exc:
                result = f"ERROR: {exc}"
            steps.append(AgentStep(tool=tool, args=args, result=result))
            messages.append(Message(role="assistant", content=response.content))
            messages.append(Message(role="user", content=f"TOOL_RESULT {tool}:\n{result}"))

        return AgentResult(
            summary=f"Stopped after reaching max_steps={max_steps}. No finish action received.",
            steps=steps,
            snapshots=list(self._snapshot_ids),
            checks_ok=self._checks_ok(),
            ui_test_ok=self._ui_test_ok,
        )
