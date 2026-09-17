from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from onec_harness.extensions import ExtensionSourceManager
from onec_harness.metadata import ConfigurationIndex
from onec_harness.onec.com import ComConnector
from onec_harness.onec.designer import Designer
from onec_harness.onec.testing import ScenarioCompiler
from onec_harness.semantic_tools import SEMANTIC_TOOLS, SemanticToolExecutor
from onec_harness.settings import Settings
from onec_harness.workspace import Workspace


MCP_PROTOCOL_VERSION = "2025-06-18"


@dataclass(slots=True, frozen=True)
class MCPTool:
    name: str
    description: str
    input_schema: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


class MCPToolRegistry:
    """Safe tool registry shared by the stdio MCP transport and unit tests."""

    def __init__(
        self,
        settings: Settings,
        *,
        workspace: Workspace | None = None,
        designer: Designer | None = None,
        runtime: ComConnector | None = None,
    ) -> None:
        self.settings = settings
        self.workspace = workspace or Workspace(settings.onec_workspace)
        self.index = ConfigurationIndex.build(self.workspace.root)
        self.semantic = SemanticToolExecutor(self.workspace)
        self.extensions = ExtensionSourceManager(self.workspace)
        self.scenarios = ScenarioCompiler(settings)
        self.designer = designer
        if self.designer is None and settings.onec_exe and settings.onec_staging_ib_connection.strip():
            self.designer = Designer(settings, connection_override=settings.onec_staging_ib_connection)
        self.runtime = runtime or ComConnector(settings, allow_writes=False)

    @staticmethod
    def tools() -> list[MCPTool]:
        obj = {"type": "object", "additionalProperties": True}
        return [
            MCPTool("metadata", "Find 1C metadata objects in the exported configuration.", {
                "type": "object", "properties": {"query": {"type": "string"}}, "additionalProperties": False,
            }),
            MCPTool("symbols", "Find BSL procedures/functions.", {
                "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"],
                "additionalProperties": False,
            }),
            MCPTool("search", "Search BSL/XML source text.", {
                "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"],
                "additionalProperties": False,
            }),
            MCPTool("read", "Read one file inside ONEC_WORKSPACE.", {
                "type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"],
                "additionalProperties": False,
            }),
            MCPTool("diff", "Return the current source diff excluding harness snapshots.", {
                "type": "object", "properties": {}, "additionalProperties": False,
            }),
            MCPTool("semantic", "Execute an allowlisted high-level 1C metadata mutation. Requires ONEC_MCP_ALLOW_WRITES=true.", {
                "type": "object",
                "properties": {"tool": {"type": "string", "enum": sorted(SEMANTIC_TOOLS)}, "args": obj},
                "required": ["tool"], "additionalProperties": False,
            }),
            MCPTool("extension_borrow", "Borrow a base object into a dumped extension. Requires MCP write opt-in.", {
                "type": "object",
                "properties": {
                    "extension": {"type": "string"}, "kind": {"type": "string"}, "object_name": {"type": "string"},
                },
                "required": ["extension", "kind", "object_name"], "additionalProperties": False,
            }),
            MCPTool("extension_patch_method", "Add Before/After/Instead method interception to a borrowed extension object.", {
                "type": "object", "additionalProperties": True,
                "properties": {
                    "extension": {"type": "string"}, "kind": {"type": "string"}, "object_name": {"type": "string"},
                    "method_name": {"type": "string"}, "interceptor": {"type": "string"}, "module": {"type": "string"},
                    "handler_name": {"type": "string"}, "parameters": {"type": "array", "items": {"type": "string"}},
                    "body": {"type": "string"}, "context": {"type": "string"}, "function": {"type": "boolean"},
                },
                "required": ["extension", "kind", "object_name", "method_name"],
            }),
            MCPTool("runtime_query", "Run a read-only 1C query through V83.COMConnector.", {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "text": {"type": "string"}, "fields": {"type": "array", "items": {"type": "string"}},
                    "parameters": obj, "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
                },
                "required": ["text", "fields"],
            }),
            MCPTool("stage_check", "Load source into staging and run CheckModules + CheckConfig. Can target one extension.", {
                "type": "object", "additionalProperties": False,
                "properties": {"extension": {"type": "string"}, "update_db": {"type": "boolean"}},
            }),
            MCPTool("compile_ui_test", "Compile a safe Test Manager action list to BSL.", {
                "type": "object", "properties": {"actions": {"type": "array", "items": obj}},
                "required": ["actions"], "additionalProperties": False,
            }),
        ]

    def refresh(self) -> None:
        self.index = ConfigurationIndex.build(self.workspace.root)

    @staticmethod
    def _objects(value: Any, label: str) -> list[dict[str, Any]]:
        if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
            raise ValueError(f"{label} must be an array of objects")
        return value

    @staticmethod
    def _strings(value: Any, label: str) -> list[str]:
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"{label} must be an array of strings")
        return value

    def _write_guard(self) -> None:
        if not self.settings.onec_mcp_allow_writes:
            raise PermissionError("MCP source writes are disabled; set ONEC_MCP_ALLOW_WRITES=true to opt in")

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        args = arguments or {}
        if name == "metadata":
            return self.index.describe_objects(str(args.get("query", "")))
        if name == "symbols":
            return self.index.describe_symbols(str(args.get("query", "")))
        if name == "search":
            matches = self.workspace.search(str(args.get("query", "")))[:100]
            return "\n".join(f"{item.path}:{item.line}: {item.text}" for item in matches) or "No matches"
        if name == "read":
            return self.workspace.read_text(str(args.get("path", "")))
        if name == "diff":
            return self.workspace.git_diff() or "No changes"
        if name == "semantic":
            self._write_guard()
            tool = str(args.get("tool", ""))
            if tool not in SEMANTIC_TOOLS:
                raise ValueError(f"Unknown semantic tool: {tool}")
            raw_args = args.get("args") or {}
            if not isinstance(raw_args, dict):
                raise ValueError("args must be an object")
            change = self.semantic.execute(tool, raw_args)
            self.refresh()
            return json.dumps({"summary": change.summary, "snapshot_id": change.snapshot_id, "paths": change.paths}, ensure_ascii=False)
        if name == "extension_borrow":
            self._write_guard()
            change = self.extensions.borrow_object(
                str(args.get("extension", "")), str(args.get("kind", "")), str(args.get("object_name", ""))
            )
            return json.dumps({"summary": change.summary, "snapshot_id": change.snapshot_id, "paths": change.paths}, ensure_ascii=False)
        if name == "extension_patch_method":
            self._write_guard()
            parameters = args.get("parameters")
            change = self.extensions.patch_method(
                str(args.get("extension", "")),
                str(args.get("kind", "")),
                str(args.get("object_name", "")),
                str(args.get("method_name", "")),
                interceptor=str(args.get("interceptor", "Before")),
                module=str(args.get("module", "object")),
                handler_name=str(args["handler_name"]) if args.get("handler_name") is not None else None,
                parameters=self._strings(parameters, "parameters") if parameters is not None else None,
                body=str(args.get("body", "// TODO: implement")),
                context=str(args["context"]) if args.get("context") is not None else None,
                function=bool(args.get("function", False)),
            )
            return json.dumps({"summary": change.summary, "snapshot_id": change.snapshot_id, "paths": change.paths}, ensure_ascii=False)
        if name == "runtime_query":
            fields = self._strings(args.get("fields"), "fields")
            parameters = args.get("parameters") or {}
            if not isinstance(parameters, dict):
                raise ValueError("parameters must be an object")
            rows = self.runtime.query(
                str(args.get("text", "")), fields=fields, parameters=parameters, limit=int(args.get("limit", 200))
            )
            return json.dumps(rows, ensure_ascii=False, default=str)
        if name == "stage_check":
            if self.designer is None:
                raise RuntimeError("staging Designer is not configured")
            extension = str(args.get("extension", "")).strip() or None
            source: Path = self.workspace.root
            if extension:
                source = self.workspace.resolve(self.extensions.extension_root(extension))
            stage = self.designer.load_config(
                source,
                execute=True,
                update_db=bool(args.get("update_db", False)),
                update_dump_info=True,
                extension=extension,
            )
            modules = self.designer.check_modules(execute=True, extension=extension) if stage.ok else None
            config = self.designer.check_config(execute=True, extension=extension) if modules and modules.ok else None
            payload = {
                "stage": stage.ok,
                "modules": modules.ok if modules else False,
                "config": config.ok if config else False,
                "log": "\n".join(
                    item.combined_output() for item in (stage, modules, config) if item is not None and item.combined_output()
                ),
            }
            return json.dumps(payload, ensure_ascii=False)
        if name == "compile_ui_test":
            return self.scenarios.compile(self._objects(args.get("actions"), "actions"))
        raise ValueError(f"Unknown MCP tool: {name}")


class MCPServer:
    def __init__(self, registry: MCPToolRegistry) -> None:
        self.registry = registry

    @staticmethod
    def _response(request_id: Any, result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}

    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = request.get("method")
        request_id = request.get("id")
        if method and str(method).startswith("notifications/"):
            return None
        if method == "initialize":
            params = request.get("params") or {}
            requested = params.get("protocolVersion") if isinstance(params, dict) else None
            protocol = requested if isinstance(requested, str) else MCP_PROTOCOL_VERSION
            return self._response(request_id, {
                "protocolVersion": protocol,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "1c-harness", "version": "0.3.0"},
            })
        if method == "ping":
            return self._response(request_id, {})
        if method == "tools/list":
            return self._response(request_id, {"tools": [tool.as_dict() for tool in self.registry.tools()]})
        if method == "tools/call":
            params = request.get("params") or {}
            if not isinstance(params, dict) or not isinstance(params.get("name"), str):
                return self._error(request_id, -32602, "Invalid tools/call parameters")
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                return self._error(request_id, -32602, "arguments must be an object")
            try:
                text = self.registry.call(params["name"], arguments)
                return self._response(request_id, {"content": [{"type": "text", "text": text}], "isError": False})
            except Exception as exc:
                return self._response(request_id, {
                    "content": [{"type": "text", "text": f"ERROR: {exc}"}],
                    "isError": True,
                })
        return self._error(request_id, -32601, f"Method not found: {method}")

    def serve(self, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> None:
        for raw in stdin:
            line = raw.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("request must be an object")
                response = self.handle(request)
            except Exception as exc:
                response = self._error(None, -32700, f"Parse error: {exc}")
            if response is not None:
                stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
                stdout.flush()


def run_stdio_server(settings: Settings | None = None) -> None:
    active = settings or Settings()
    workspace = Workspace(active.onec_workspace)
    workspace.ensure_exists()
    MCPServer(MCPToolRegistry(active, workspace=workspace)).serve()
