import json
from pathlib import Path

from onec_harness.mcp_server import MCPServer, MCPToolRegistry
from onec_harness.settings import Settings
from onec_harness.workspace import Workspace


CONFIG = '''<?xml version="1.0" encoding="UTF-8"?>
<MetaDataObject xmlns="http://v8.1c.ru/8.3/MDClasses" version="2.17">
  <Configuration uuid="00000000-0000-0000-0000-000000000001">
    <Properties><Name>HarnessTest</Name></Properties>
    <ChildObjects/>
  </Configuration>
</MetaDataObject>
'''


def _registry(tmp_path: Path, *, writes: bool = False) -> MCPToolRegistry:
    workspace = Workspace(tmp_path)
    workspace.write_text("Configuration.xml", CONFIG)
    settings = Settings(_env_file=None, onec_workspace=tmp_path, onec_mcp_allow_writes=writes)
    return MCPToolRegistry(settings, workspace=workspace)


def test_mcp_initialize_and_tool_list(tmp_path: Path) -> None:
    server = MCPServer(_registry(tmp_path))

    initialized = server.handle({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-06-18"},
    })
    listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})

    assert initialized is not None
    assert initialized["result"]["serverInfo"]["name"] == "1c-harness"
    assert listed is not None
    names = {tool["name"] for tool in listed["result"]["tools"]}
    assert {"metadata", "semantic", "stage_check", "compile_ui_test"} <= names


def test_mcp_read_tool_returns_content(tmp_path: Path) -> None:
    server = MCPServer(_registry(tmp_path))

    response = server.handle({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "read", "arguments": {"path": "Configuration.xml"}},
    })

    assert response is not None
    assert response["result"]["isError"] is False
    assert "HarnessTest" in response["result"]["content"][0]["text"]


def test_mcp_semantic_write_requires_opt_in(tmp_path: Path) -> None:
    server = MCPServer(_registry(tmp_path, writes=False))

    response = server.handle({
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {"name": "semantic", "arguments": {"tool": "create_catalog", "args": {"name": "Товары"}}},
    })

    assert response is not None
    assert response["result"]["isError"] is True
    assert "ONEC_MCP_ALLOW_WRITES" in response["result"]["content"][0]["text"]


def test_mcp_semantic_write_returns_snapshot_when_enabled(tmp_path: Path) -> None:
    server = MCPServer(_registry(tmp_path, writes=True))

    response = server.handle({
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {"name": "semantic", "arguments": {"tool": "create_catalog", "args": {"name": "Товары"}}},
    })

    assert response is not None
    assert response["result"]["isError"] is False
    payload = json.loads(response["result"]["content"][0]["text"])
    assert payload["snapshot_id"]
    assert (tmp_path / "Catalogs" / "Товары.xml").exists()
