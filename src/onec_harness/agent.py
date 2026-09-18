from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from collections.abc import Callable

from onec_harness.extensions import ExtensionSourceManager
from onec_harness.metadata import ConfigurationIndex
from onec_harness.onec.com import ComConnector, ComConnectorError
from onec_harness.onec.designer import Designer, DesignerError
from onec_harness.onec.e2e import TestManagerRunner
from onec_harness.onec.testing import ScenarioCompiler, TestClientError
from onec_harness.providers.base import LLMProvider, Message, ProviderError
from onec_harness.semantic import SemanticMetadataError
from onec_harness.semantic_tools import SEMANTIC_TOOLS, SemanticToolExecutor
from onec_harness.snapshots import SnapshotError, SnapshotStore
from onec_harness.workspace import Workspace, WorkspaceError


SYSTEM_PROMPT = """Ты автономный инженер по 1С:Предприятие/BSL, работающий через безопасный harness.
Сначала исследуй реальную конфигурацию через metadata/symbols/search/read. Не выдумывай имена объектов и полей.
На каждом шаге отвечай только одним JSON-объектом: {"tool":"...","args":{...}}.

Основные source tools: metadata, symbols, search, read, patch, diff, rollback.
Semantic metadata tools: create_catalog, create_document_meta, create_enum, add_enum_value, add_attribute,
add_tabular_section, create_information_register, create_accumulation_register, create_managed_form,
add_form_input, add_form_command, ensure_module.
Примеры форм:
{"tool":"create_managed_form","args":{"kind":"document","object_name":"Заявка","name":"ФормаДокумента","purpose":"Object","set_default":true}}
{"tool":"add_form_input","args":{"kind":"document","object_name":"Заявка","form_name":"ФормаДокумента","name":"Комментарий","data_path":"Объект.Комментарий"}}
{"tool":"add_form_command","args":{"kind":"document","object_name":"Заявка","form_name":"ФормаДокумента","name":"Проверить","handler_body":"Сообщить(\"OK\");"}}

Расширения (исходники должны быть выгружены в Extensions/<Имя>):
{"tool":"borrow_extension_object","args":{"extension":"МоеРасширение","kind":"document","object_name":"Заказ"}}
{"tool":"patch_extension_method","args":{"extension":"МоеРасширение","kind":"document","object_name":"Заказ","method_name":"ОбработкаПроведения","interceptor":"Before","module":"object","parameters":["Отказ","РежимПроведения"],"body":"// код"}}
{"tool":"stage_extension","args":{"extension":"МоеРасширение","update_db":false}}
{"tool":"check_extension_modules","args":{"extension":"МоеРасширение"}}
{"tool":"check_extension_config","args":{"extension":"МоеРасширение"}}
{"tool":"check_extension_applicability","args":{"extension":"МоеРасширение"}}

Основная конфигурация: stage_config -> check_modules -> check_config.
Read-only runtime: runtime_query, catalog_items, document_items, register_records.
Runtime writes: create_catalog_item, create_document_record — только при явном разрешении.
UI: ui_test_scenario генерирует BSL; run_ui_test реально запускает Test Client/Test Manager.
Завершение: {"tool":"finish","args":{"summary":"что сделано и как проверено"}}.

Правила:
- Все source/metadata изменения должны иметь snapshot.
- После изменений обязательно diff.
- При --check нельзя finish, пока каждый изменённый scope (config или extension) не прошёл stage + CheckModules + CheckConfig.
- Основная база никогда не используется для автономной проверки; только staging.
- Для E2E после изменённых исходников нужен stage с update_db=true для каждого изменённого scope.
- Не утверждай успех проверки без OK от harness.
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
    status: str = "completed"


@dataclass(slots=True)
class _ValidationState:
    stage_ok: bool | None = None
    db_updated: bool = False
    modules_ok: bool | None = None
    config_ok: bool | None = None


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
        on_event: Callable[[dict[str, Any]], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> None:
        self.on_event = on_event or (lambda event: None)
        self.cancelled = cancelled or (lambda: False)
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
        self.extension_tools = ExtensionSourceManager(workspace)
        self.index = ConfigurationIndex.build(workspace.root)
        self._source_changed = False
        self._config_changed = False
        self._extension_changed: set[str] = set()
        self._diff_seen = False
        self._config_validation = _ValidationState()
        self._extension_validation: dict[str, _ValidationState] = {}
        self._ui_test_attempted = False
        self._ui_test_ok: bool | None = None
        self._snapshot_ids: list[str] = []

    @staticmethod
    def _parse_action(text: str) -> dict[str, Any]:
        candidate = text.strip()
        if candidate.startswith("```"):
            lines = candidate.splitlines()[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            candidate = "\n".join(lines).strip()
        try:
            action = json.loads(candidate)
        except json.JSONDecodeError:
            start, end = candidate.find("{"), candidate.rfind("}")
            if start < 0 or end <= start:
                raise AgentProtocolError("Model did not return a JSON action") from None
            try:
                action = json.loads(candidate[start:end + 1])
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
        return value if len(value) <= self.max_result_chars else value[:self.max_result_chars] + "\n...[truncated by harness]"

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

    def _touch_common(self, snapshot_id: str) -> None:
        self._source_changed = True
        self._snapshot_ids.append(snapshot_id)
        self._diff_seen = False
        self._ui_test_attempted = False
        self._ui_test_ok = None
        self.index = ConfigurationIndex.build(self.workspace.root)

    def _mark_source_change(self, snapshot_id: str) -> None:
        self._touch_common(snapshot_id)
        self._config_changed = True
        self._config_validation = _ValidationState()

    def _mark_extension_change(self, extension: str, change: Any) -> str:
        self._touch_common(change.snapshot_id)
        self._extension_changed.add(extension)
        self._extension_validation[extension] = _ValidationState()
        return f"{change.summary}. snapshot_id={change.snapshot_id}; paths={', '.join(change.paths)}"

    def _semantic_result(self, change: Any) -> str:
        self._mark_source_change(change.snapshot_id)
        return f"{change.summary}. snapshot_id={change.snapshot_id}; paths={', '.join(change.paths)}"

    def _recompute_scopes(self) -> None:
        paths = self.workspace.changed_paths()
        extensions: set[str] = set()
        config_changed = False
        for path in paths:
            parts = path.replace("\\", "/").split("/")
            if len(parts) >= 3 and parts[0] == "Extensions":
                extensions.add(parts[1])
            else:
                config_changed = True
        self._source_changed = bool(paths)
        self._config_changed = config_changed
        self._extension_changed = extensions
        self._config_validation = _ValidationState()
        self._extension_validation = {name: _ValidationState() for name in extensions}
        self._diff_seen = False

    def _require_runtime(self) -> ComConnector:
        if self.runtime is None:
            raise ComConnectorError("COM runtime adapter is not configured")
        return self.runtime

    def _require_checks(self) -> Designer:
        if not self.execute_checks:
            raise DesignerError("1C checks are disabled. Run agent with --check.")
        if self.designer is None:
            raise DesignerError("staging Designer is not configured")
        return self.designer

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
            if not path.lower().endswith((".bsl", ".xml")) or any(
                part.startswith(".") for part in path.replace("\\", "/").split("/")
            ):
                return "ERROR: only non-internal BSL/XML sources are available"
            return self._clip(self.workspace.read_text(path)) if path else "ERROR: path is required"

        if tool == "patch":
            if not self.allow_writes:
                return "ERROR: source writes are disabled. Run agent with --write."
            path, old, new = str(args.get("path", "")), str(args.get("old", "")), str(args.get("new", ""))
            if not path.lower().endswith((".bsl", ".xml")) or any(
                part.startswith(".") for part in path.replace("\\", "/").split("/")
            ):
                return "ERROR: only BSL/XML source files can be patched"
            if not path or not old:
                return "ERROR: patch requires path and non-empty old text"
            snapshot = self.snapshots.create([path])
            self.workspace.replace_once(path, old, new)
            parts = self.workspace.resolve(path).relative_to(self.workspace.root).parts
            if len(parts) >= 3 and parts[0] == "Extensions":
                self._touch_common(snapshot.snapshot_id)
                self._extension_changed.add(parts[1])
                self._extension_validation[parts[1]] = _ValidationState()
            else:
                self._mark_source_change(snapshot.snapshot_id)
            return f"Patched {path}. snapshot_id={snapshot.snapshot_id}"

        if tool in SEMANTIC_TOOLS:
            if not self.allow_writes:
                return "ERROR: source writes are disabled. Run agent with --write."
            return self._semantic_result(self.semantic_tools.execute(tool, args))

        if tool in {"borrow_extension_object", "patch_extension_method"}:
            if not self.allow_writes:
                return "ERROR: source writes are disabled. Run agent with --write."
            extension = str(args.get("extension", ""))
            if tool == "borrow_extension_object":
                change = self.extension_tools.borrow_object(
                    extension,
                    str(args.get("kind", "")),
                    str(args.get("object_name", "")),
                )
            else:
                params = args.get("parameters")
                parameters = self._string_list(params, label="parameters") if params is not None else None
                change = self.extension_tools.patch_method(
                    extension,
                    str(args.get("kind", "")),
                    str(args.get("object_name", "")),
                    str(args.get("method_name", "")),
                    interceptor=str(args.get("interceptor", "Before")),
                    module=str(args.get("module", "object")),
                    handler_name=str(args["handler_name"]) if args.get("handler_name") is not None else None,
                    parameters=parameters,
                    body=str(args.get("body", "// TODO: implement")),
                    context=str(args["context"]) if args.get("context") is not None else None,
                    function=bool(args.get("function", False)),
                )
            return self._mark_extension_change(extension, change)

        if tool == "diff":
            diff = self.workspace.git_diff() or "No changes"
            self._diff_seen = True
            return self._clip(diff)

        if tool == "stage_config":
            designer = self._require_checks()
            update_db = bool(args.get("update_db", False))
            result = designer.load_config(self.workspace.root, execute=True, update_db=update_db, update_dump_info=True)
            self._config_validation = _ValidationState(stage_ok=result.ok, db_updated=bool(result.ok and update_db))
            output = result.combined_output() or f"1C Designer exited with code {result.returncode}"
            return self._clip(self._format_check("StageConfig", result.ok, output))

        if tool in {"check_modules", "check_config"}:
            designer = self._require_checks()
            if self._config_changed and self._config_validation.stage_ok is not True:
                return "ERROR: changed workspace has not been successfully loaded into staging. Call stage_config first."
            if tool == "check_modules":
                result = designer.check_modules(execute=True)
                self._config_validation.modules_ok = result.ok
                label = "CheckModules"
            else:
                result = designer.check_config(execute=True)
                self._config_validation.config_ok = result.ok
                label = "CheckConfig"
            output = result.combined_output() or f"1C Designer exited with code {result.returncode}"
            return self._clip(self._format_check(label, result.ok, output))

        if tool in {"stage_extension", "check_extension_modules", "check_extension_config", "check_extension_applicability"}:
            designer = self._require_checks()
            extension = str(args.get("extension", "")).strip()
            if not extension:
                return "ERROR: extension is required"
            state = self._extension_validation.setdefault(extension, _ValidationState())
            if tool == "stage_extension":
                root = self.extension_tools.extension_root(extension)
                update_db = bool(args.get("update_db", False))
                result = designer.load_config(
                    self.workspace.resolve(root),
                    execute=True,
                    update_db=update_db,
                    update_dump_info=True,
                    extension=extension,
                )
                self._extension_validation[extension] = _ValidationState(
                    stage_ok=result.ok,
                    db_updated=bool(result.ok and update_db),
                )
                label = f"StageExtension[{extension}]"
            else:
                if extension in self._extension_changed and state.stage_ok is not True:
                    return f"ERROR: extension {extension} has not been successfully staged. Call stage_extension first."
                if tool == "check_extension_modules":
                    result = designer.check_modules(execute=True, extension=extension)
                    state.modules_ok = result.ok
                    label = f"CheckExtensionModules[{extension}]"
                elif tool == "check_extension_config":
                    result = designer.check_config(execute=True, extension=extension)
                    state.config_ok = result.ok
                    label = f"CheckExtensionConfig[{extension}]"
                else:
                    result = designer.check_extension_applicability(extension, execute=True)
                    label = f"CheckExtensionApplicability[{extension}]"
            output = result.combined_output() or f"1C Designer exited with code {result.returncode}"
            return self._clip(self._format_check(label, result.ok, output))

        if tool == "rollback":
            snapshot_id = str(args.get("snapshot_id", ""))
            if not snapshot_id:
                return "ERROR: snapshot_id is required"
            restored = self.snapshots.restore(snapshot_id)
            self.index = ConfigurationIndex.build(self.workspace.root)
            self._recompute_scopes()
            return f"Restored snapshot {restored.snapshot_id}: {', '.join(restored.paths)}"

        if tool == "runtime_query":
            rows = self._require_runtime().query(
                str(args.get("text", "")),
                fields=self._string_list(args.get("fields"), label="fields"),
                parameters=self._mapping(args.get("parameters"), label="parameters"),
                limit=int(args.get("limit", 200)),
            )
            return self._clip(json.dumps(rows, ensure_ascii=False, default=str))

        if tool in {"catalog_items", "document_items"}:
            runtime = self._require_runtime()
            common = {
                "name": str(args.get("name", "")),
                "fields": self._string_list(args.get("fields"), label="fields"),
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
            ref = (
                runtime.create_catalog_item(str(args.get("name", "")), attributes)
                if tool == "create_catalog_item"
                else runtime.create_document(str(args.get("name", "")), attributes, post=bool(args.get("post", False)))
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
            if self._config_changed and not self._config_validation.db_updated:
                return "ERROR: changed configuration requires stage_config(update_db=true) before E2E UI testing."
            missing = [name for name in self._extension_changed if not self._extension_validation.get(name, _ValidationState()).db_updated]
            if missing:
                return "ERROR: changed extensions require stage_extension(update_db=true) before E2E UI testing: " + ", ".join(sorted(missing))
            result = self.test_runner.run(self._action_list(args.get("actions")), execute=True)
            self._ui_test_attempted = True
            self._ui_test_ok = result.success is True
            return self._clip(json.dumps(result.as_dict(), ensure_ascii=False, default=str))

        return f"ERROR: unknown tool {tool!r}"

    def _checks_ok(self) -> bool | None:
        if not self.execute_checks or not self._source_changed:
            return None
        if self._config_changed:
            state = self._config_validation
            if not (state.stage_ok and state.modules_ok and state.config_ok):
                return False
        for extension in self._extension_changed:
            state = self._extension_validation.get(extension, _ValidationState())
            if not (state.stage_ok and state.modules_ok and state.config_ok):
                return False
        return True

    def _finish_block_reason(self) -> str | None:
        if self._source_changed and not self._diff_seen:
            return "You changed files but have not inspected diff yet. Call diff before finish."
        if self.execute_checks:
            if self._config_changed:
                state = self._config_validation
                if state.stage_ok is not True:
                    return "Changed configuration is not staged successfully. Call stage_config."
                if state.modules_ok is not True:
                    return "Run check_modules successfully against staging before finish."
                if state.config_ok is not True:
                    return "Run check_config successfully against staging before finish."
            for extension in sorted(self._extension_changed):
                state = self._extension_validation.get(extension, _ValidationState())
                if state.stage_ok is not True:
                    return f"Extension {extension} is not staged successfully. Call stage_extension."
                if state.modules_ok is not True:
                    return f"Run check_extension_modules successfully for {extension} before finish."
                if state.config_ok is not True:
                    return f"Run check_extension_config successfully for {extension} before finish."
        if self.execute_ui_tests and self._source_changed and not self._ui_test_attempted:
            return "UI testing was requested. Run run_ui_test before finish."
        if self._ui_test_attempted and self._ui_test_ok is not True:
            return "The latest E2E UI test failed. Fix the problem or rollback before finish."
        return None

    async def run(self, task: str, *, max_steps: int = 24) -> AgentResult:
        if max_steps < 1:
            raise ValueError("max_steps must be >= 1")
        messages = [Message(role="system", content=SYSTEM_PROMPT), Message(role="user", content=f"Задача:\n{task}")]
        steps: list[AgentStep] = []
        for iteration in range(max_steps):
            if self.cancelled():
                return AgentResult("Остановлено пользователем. Изменения доступны для review.", steps,
                                   list(self._snapshot_ids), self._checks_ok(), self._ui_test_ok, "cancelled")
            self.on_event({"type": "thinking", "iteration": iteration + 1})
            try:
                response = await self.provider.complete(messages)
            except (ProviderError, OSError) as exc:
                return AgentResult(f"Ошибка модели: {exc}", steps, list(self._snapshot_ids),
                                   self._checks_ok(), self._ui_test_ok, "failed")
            if self.cancelled():
                return AgentResult("Остановлено пользователем.", steps, list(self._snapshot_ids),
                                   self._checks_ok(), self._ui_test_ok, "cancelled")
            try:
                action = self._parse_action(response.content)
            except AgentProtocolError as exc:
                messages.extend([
                    Message(role="assistant", content=response.content),
                    Message(role="user", content=f"PROTOCOL_ERROR: {exc}. Верни только корректный JSON action."),
                ])
                continue
            tool, args = action["tool"], action["args"]
            if tool == "finish":
                blocked = self._finish_block_reason()
                if blocked:
                    messages.extend([
                        Message(role="assistant", content=response.content),
                        Message(role="user", content=f"FINISH_BLOCKED: {blocked}"),
                    ])
                    continue
                summary = str(args.get("summary", "")).strip() or "Agent finished without a summary."
                return AgentResult(summary, steps, list(self._snapshot_ids), self._checks_ok(), self._ui_test_ok)
            self.on_event({"type": "tool_start", "tool": tool})
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
            self.on_event({"type": "tool_end", "tool": tool, "args": args, "result": result})
            messages.extend([
                Message(role="assistant", content=response.content),
                Message(role="user", content=f"TOOL_RESULT {tool}:\n{result}"),
            ])
        return AgentResult(
            summary=f"Stopped after reaching max_steps={max_steps}. No finish action received.",
            status="incomplete",
            steps=steps,
            snapshots=list(self._snapshot_ids),
            checks_ok=self._checks_ok(),
            ui_test_ok=self._ui_test_ok,
        )
