from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from onec_harness.agent import HarnessAgent
from onec_harness.metadata import ConfigurationIndex
from onec_harness.onec.com import ComConnector, ComConnectorError
from onec_harness.onec.designer import CommandResult, Designer, DesignerError
from onec_harness.onec.testing import ScenarioCompiler, TestClientError, TestClientLauncher
from onec_harness.providers.base import Message, ProviderError
from onec_harness.providers.factory import create_provider
from onec_harness.semantic import MetadataEditor, SemanticMetadataError
from onec_harness.settings import Settings
from onec_harness.snapshots import SnapshotError, SnapshotStore
from onec_harness.workspace import Workspace, WorkspaceError

app = typer.Typer(no_args_is_help=True, help="AI harness for 1C:Enterprise")
console = Console()


def _settings() -> Settings:
    return Settings()


def _workspace(settings: Settings) -> Workspace:
    workspace = Workspace(settings.onec_workspace)
    workspace.ensure_exists()
    return workspace


def _print_result(result: CommandResult) -> None:
    console.print("[bold]Command:[/bold]")
    console.print(subprocess.list2cmdline(result.command))
    if not result.executed:
        console.print("[yellow]Dry-run only. Add --execute to run it.[/yellow]")
        return
    style = "green" if result.returncode == 0 else "red"
    console.print(f"[{style}]Exit code: {result.returncode}[/{style}]")
    if result.stdout.strip():
        console.print(result.stdout.rstrip())
    if result.stderr.strip():
        console.print(f"[red]{result.stderr.rstrip()}[/red]")
    if result.log.strip():
        console.print("[bold]1C log:[/bold]")
        console.print(result.log.rstrip())


def _agent_payload(result: Any) -> dict[str, Any]:
    return {
        "summary": result.summary,
        "snapshots": result.snapshots,
        "checks_ok": result.checks_ok,
        "steps": [{"tool": step.tool, "args": step.args, "result": step.result} for step in result.steps],
    }


def _require_yes(yes: bool, message: str) -> None:
    if not yes:
        console.print(f"[red]{message} requires --yes[/red]")
        raise typer.Exit(2)


def _json_object(raw: str, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"{label} must be valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise typer.BadParameter(f"{label} must be a JSON object")
    return value


def _csv(value: str) -> list[str]:
    result = [item.strip() for item in value.split(",") if item.strip()]
    if not result:
        raise typer.BadParameter("At least one comma-separated value is required")
    return result


@app.command()
def doctor(json_output: bool = typer.Option(False, "--json")) -> None:
    """Show wiring status without exposing secrets."""
    settings = _settings()
    if settings.provider_name == "gigachat":
        credentials_ok = bool(settings.gigachat_credentials)
    elif settings.provider_name == "anthropic":
        credentials_ok = bool(settings.anthropic_api_key)
    else:
        credentials_ok = bool(settings.llm_api_key)
    com_configured = ComConnector(settings).configured

    payload = {
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "llm_credentials": credentials_ok,
        "onec_exe": str(settings.onec_exe) if settings.onec_exe else None,
        "onec_connection": bool(settings.onec_ib_connection),
        "staging_connection": bool(settings.onec_staging_ib_connection),
        "com_configured": com_configured,
        "runtime_writes": settings.onec_runtime_allow_writes,
        "test_client_connection": bool(settings.onec_test_client_connection or settings.onec_staging_ib_connection),
        "test_manager_connection": bool(settings.onec_test_manager_connection),
        "test_port": settings.onec_test_port,
        "workspace": str(settings.onec_workspace.expanduser().resolve()),
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False))
        return

    table = Table(title="1C Harness doctor")
    table.add_column("Capability")
    table.add_column("Status")
    table.add_row("LLM", f"{settings.llm_provider} / {settings.llm_model}")
    table.add_row("LLM credentials", "configured" if credentials_ok else "missing")
    table.add_row("1C executable", str(settings.onec_exe or "not configured"))
    table.add_row("Primary infobase", "configured" if settings.onec_ib_connection else "missing")
    table.add_row("Staging infobase", "configured" if settings.onec_staging_ib_connection else "missing")
    table.add_row("COM runtime", "configured" if com_configured else "missing")
    table.add_row("Runtime writes", "enabled" if settings.onec_runtime_allow_writes else "disabled")
    table.add_row("Test Client", "configured" if payload["test_client_connection"] else "missing")
    table.add_row("Test Manager", "configured" if settings.onec_test_manager_connection else "missing")
    table.add_row("Workspace", payload["workspace"])
    console.print(table)


@app.command()
def ask(prompt: str) -> None:
    """Send a direct test prompt through the selected model provider."""
    settings = _settings()

    async def run() -> str:
        provider = create_provider(settings)
        response = await provider.complete(
            [
                Message(
                    role="system",
                    content="Ты инженер по 1С:Предприятие и BSL. Не выдумывай объекты конфигурации.",
                ),
                Message(role="user", content=prompt),
            ]
        )
        return response.content

    try:
        console.print(asyncio.run(run()))
    except ProviderError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@app.command()
def agent(
    task: str,
    write: bool = typer.Option(False, "--write", help="Allow snapshotted source/metadata changes"),
    check: bool = typer.Option(False, "--check", help="Load changes into staging and validate with 1C Designer"),
    runtime_write: bool = typer.Option(False, "--runtime-write", help="Allow explicitly enabled COM runtime mutations"),
    max_steps: int = typer.Option(24, "--max-steps", min=1, max=80),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable output for desktop/MCP clients"),
) -> None:
    """Run the selected model through the allowlisted 1C engineering tool loop."""
    settings = _settings()
    workspace = _workspace(settings)
    if check and not settings.onec_staging_ib_connection.strip():
        message = "--check requires ONEC_STAGING_IB_CONNECTION; autonomous checks never load into the primary infobase"
        if json_output:
            typer.echo(json.dumps({"error": message}, ensure_ascii=False))
        else:
            console.print(f"[red]{message}[/red]")
        raise typer.Exit(2)
    if runtime_write and not settings.onec_runtime_allow_writes:
        message = "--runtime-write also requires ONEC_RUNTIME_ALLOW_WRITES=true"
        if json_output:
            typer.echo(json.dumps({"error": message}, ensure_ascii=False))
        else:
            console.print(f"[red]{message}[/red]")
        raise typer.Exit(2)

    async def run():
        provider = create_provider(settings)
        designer = (
            Designer(settings, connection_override=settings.onec_staging_ib_connection)
            if check
            else None
        )
        runtime = ComConnector(settings, allow_writes=runtime_write and settings.onec_runtime_allow_writes)
        compiler = ScenarioCompiler(settings.onec_test_host, settings.onec_test_port)
        harness = HarnessAgent(
            provider,
            workspace,
            designer=designer,
            runtime=runtime,
            scenario_compiler=compiler,
            allow_writes=write,
            execute_checks=check,
            allow_runtime_writes=runtime_write,
        )
        return await harness.run(task, max_steps=max_steps)

    try:
        result = asyncio.run(run())
    except (
        ProviderError,
        WorkspaceError,
        DesignerError,
        SnapshotError,
        ComConnectorError,
        SemanticMetadataError,
        TestClientError,
    ) as exc:
        if json_output:
            typer.echo(json.dumps({"error": str(exc)}, ensure_ascii=False))
        else:
            console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    if json_output:
        typer.echo(json.dumps(_agent_payload(result), ensure_ascii=False))
        return

    table = Table(title="Agent steps")
    table.add_column("#", justify="right")
    table.add_column("Tool")
    table.add_column("Result")
    for index, step in enumerate(result.steps, start=1):
        compact = step.result.replace("\n", " ")
        table.add_row(str(index), step.tool, compact[:157] + "..." if len(compact) > 160 else compact)
    if result.steps:
        console.print(table)
    console.print("[bold]Summary:[/bold]")
    console.print(result.summary)
    if result.snapshots:
        console.print(f"[dim]Snapshots: {', '.join(result.snapshots)}[/dim]")
    if not write:
        console.print("[yellow]Read-only source mode: patches were not permitted.[/yellow]")


@app.command("metadata")
def metadata_command(query: str = typer.Argument(""), limit: int = 100) -> None:
    """List indexed 1C metadata objects from the exported configuration."""
    console.print(ConfigurationIndex.build(_workspace(_settings()).root).describe_objects(query=query, limit=limit))


@app.command("symbols")
def symbols_command(query: str, limit: int = 100) -> None:
    """Find BSL procedures/functions by symbol name."""
    console.print(ConfigurationIndex.build(_workspace(_settings()).root).describe_symbols(query=query, limit=limit))


@app.command("create-catalog")
def create_catalog(
    name: str,
    synonym: str | None = None,
    hierarchical: bool = False,
    yes: bool = typer.Option(False, "--yes"),
) -> None:
    """Create catalog metadata in the local exported workspace."""
    _require_yes(yes, "Metadata creation")
    try:
        change = MetadataEditor(_workspace(_settings())).create_catalog(name, synonym=synonym, hierarchical=hierarchical)
        console.print(f"{change.summary}\nSnapshot: {change.snapshot_id}")
    except (SemanticMetadataError, SnapshotError, WorkspaceError, OSError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@app.command("create-document-meta")
def create_document_meta(
    name: str,
    synonym: str | None = None,
    posting: bool = False,
    yes: bool = typer.Option(False, "--yes"),
) -> None:
    """Create document metadata in the local exported workspace."""
    _require_yes(yes, "Metadata creation")
    try:
        change = MetadataEditor(_workspace(_settings())).create_document(name, synonym=synonym, posting=posting)
        console.print(f"{change.summary}\nSnapshot: {change.snapshot_id}")
    except (SemanticMetadataError, SnapshotError, WorkspaceError, OSError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@app.command("add-attribute")
def add_attribute(
    kind: str,
    object_name: str,
    name: str,
    value_type: str = "string",
    synonym: str | None = None,
    string_length: int = 100,
    yes: bool = typer.Option(False, "--yes"),
) -> None:
    """Add a typed attribute to a catalog/document in the local workspace."""
    _require_yes(yes, "Metadata change")
    try:
        change = MetadataEditor(_workspace(_settings())).add_attribute(
            kind,
            object_name,
            name,
            value_type=value_type,
            synonym=synonym,
            string_length=string_length,
        )
        console.print(f"{change.summary}\nSnapshot: {change.snapshot_id}")
    except (SemanticMetadataError, SnapshotError, WorkspaceError, OSError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@app.command("read")
def read_file(path: str) -> None:
    try:
        console.print(_workspace(_settings()).read_text(path))
    except (WorkspaceError, OSError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@app.command()
def search(query: str, limit: int = 50) -> None:
    matches = _workspace(_settings()).search(query)[:limit]
    for match in matches:
        console.print(f"[cyan]{match.path}:{match.line}[/cyan] {match.text}")
    if not matches:
        console.print("No matches")


@app.command()
def diff() -> None:
    try:
        console.print(_workspace(_settings()).git_diff() or "No changes")
    except WorkspaceError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@app.command()
def rollback(path: str, yes: bool = typer.Option(False, "--yes")) -> None:
    try:
        _workspace(_settings()).git_restore(path, confirmed=yes)
        console.print(f"Restored {path}")
    except WorkspaceError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@app.command("restore-snapshot")
def restore_snapshot(snapshot_id: str, yes: bool = typer.Option(False, "--yes")) -> None:
    _require_yes(yes, "Snapshot restore")
    try:
        info = SnapshotStore(_workspace(_settings())).restore(snapshot_id)
        console.print(f"Restored {snapshot_id}: {', '.join(info.paths)}")
    except (SnapshotError, WorkspaceError, OSError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@app.command("runtime-query")
def runtime_query(
    text: str,
    fields: str = typer.Option(..., "--fields", help="Comma-separated result field aliases"),
    parameters: str = typer.Option("{}", "--parameters", help="JSON object"),
    limit: int = 200,
) -> None:
    """Execute a read-only 1C query through V83.COMConnector."""
    try:
        rows = ComConnector(_settings()).query(
            text,
            fields=_csv(fields),
            parameters=_json_object(parameters, "parameters"),
            limit=limit,
        )
        typer.echo(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
    except ComConnectorError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@app.command("runtime-create-catalog-item")
def runtime_create_catalog_item(
    name: str,
    attributes: str = typer.Option(..., "--attributes", help="JSON object"),
    yes: bool = typer.Option(False, "--yes"),
) -> None:
    """Create an item through 1C runtime; doubly opt-in and disabled by default."""
    _require_yes(yes, "Runtime write")
    settings = _settings()
    if not settings.onec_runtime_allow_writes:
        raise typer.BadParameter("Set ONEC_RUNTIME_ALLOW_WRITES=true as the second explicit opt-in")
    try:
        ref = ComConnector(settings, allow_writes=True).create_catalog_item(name, _json_object(attributes, "attributes"))
        console.print(ref)
    except ComConnectorError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@app.command("compile-test-scenario")
def compile_test_scenario(actions_file: Path, output: Path | None = None) -> None:
    """Compile JSON UI actions to Test Manager BSL without executing them."""
    try:
        raw = json.loads(actions_file.read_text(encoding="utf-8"))
        if not isinstance(raw, list) or not all(isinstance(item, dict) for item in raw):
            raise TestClientError("Scenario JSON must be an array of action objects")
        settings = _settings()
        compiler = ScenarioCompiler(settings.onec_test_host, settings.onec_test_port)
        content = compiler.compile(raw)
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(content, encoding="utf-8")
            console.print(str(output))
        else:
            console.print(content)
    except (OSError, json.JSONDecodeError, TestClientError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@app.command("test-client")
def test_client(execute: bool = typer.Option(False, "--execute")) -> None:
    """Launch 1C in official Test Client mode; dry-run by default."""
    try:
        result = TestClientLauncher(_settings()).launch_client(execute=execute)
        console.print(subprocess.list2cmdline(result.command))
        if result.executed:
            console.print(f"PID: {result.pid}")
    except TestClientError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@app.command("test-manager")
def test_manager(execute: bool = typer.Option(False, "--execute")) -> None:
    """Launch 1C in official Test Manager mode; dry-run by default."""
    try:
        result = TestClientLauncher(_settings()).launch_manager(execute=execute)
        console.print(subprocess.list2cmdline(result.command))
        if result.executed:
            console.print(f"PID: {result.pid}")
    except TestClientError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@app.command("dump-config")
def dump_config(execute: bool = typer.Option(False, "--execute")) -> None:
    settings = _settings()
    try:
        result = Designer(settings).dump_config(settings.onec_workspace, execute=execute)
        _print_result(result)
        if result.executed and result.returncode != 0:
            raise typer.Exit(result.returncode or 1)
    except DesignerError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@app.command("stage-config")
def stage_config(
    execute: bool = typer.Option(False, "--execute"),
    update_db: bool = typer.Option(False, "--update-db"),
    yes: bool = typer.Option(False, "--yes"),
) -> None:
    """Load workspace into the explicitly configured staging infobase."""
    if execute:
        _require_yes(yes, "Staging load")
    settings = _settings()
    if not settings.onec_staging_ib_connection:
        raise typer.BadParameter("ONEC_STAGING_IB_CONNECTION is required")
    try:
        result = Designer(settings, connection_override=settings.onec_staging_ib_connection).load_config(
            settings.onec_workspace,
            execute=execute,
            update_db=update_db,
            update_dump_info=True,
        )
        _print_result(result)
        if result.executed and result.returncode != 0:
            raise typer.Exit(result.returncode or 1)
    except DesignerError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@app.command("check-modules")
def check_modules(execute: bool = typer.Option(False, "--execute"), staging: bool = True) -> None:
    """Run /CheckModules; staging is the safe default."""
    settings = _settings()
    connection = settings.onec_staging_ib_connection if staging else settings.onec_ib_connection
    if staging and not connection:
        raise typer.BadParameter("ONEC_STAGING_IB_CONNECTION is required unless --no-staging is used")
    try:
        result = Designer(settings, connection_override=connection).check_modules(execute=execute)
        _print_result(result)
        if result.executed and result.returncode != 0:
            raise typer.Exit(result.returncode or 1)
    except DesignerError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@app.command("check-config")
def check_config(execute: bool = typer.Option(False, "--execute"), staging: bool = True) -> None:
    """Run structural /CheckConfig; staging is the safe default."""
    settings = _settings()
    connection = settings.onec_staging_ib_connection if staging else settings.onec_ib_connection
    if staging and not connection:
        raise typer.BadParameter("ONEC_STAGING_IB_CONNECTION is required unless --no-staging is used")
    try:
        result = Designer(settings, connection_override=connection).check_config(execute=execute)
        _print_result(result)
        if result.executed and result.returncode != 0:
            raise typer.Exit(result.returncode or 1)
    except DesignerError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@app.command("backup-infobase")
def backup_infobase(
    target: Path,
    execute: bool = typer.Option(False, "--execute"),
    yes: bool = typer.Option(False, "--yes"),
) -> None:
    """Create a .dt backup of the primary infobase before an explicitly approved apply."""
    if execute:
        _require_yes(yes, "Infobase backup")
    try:
        result = Designer(_settings()).dump_infobase(target, execute=execute)
        _print_result(result)
        if result.executed and result.returncode != 0:
            raise typer.Exit(result.returncode or 1)
    except DesignerError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@app.command("load-config")
def load_config(
    execute: bool = typer.Option(False, "--execute"),
    update_db: bool = typer.Option(False, "--update-db"),
    yes: bool = typer.Option(False, "--yes"),
) -> None:
    """Explicitly apply workspace to the primary infobase. Never used by the autonomous check loop."""
    if execute:
        _require_yes(yes, "Primary infobase load")
    settings = _settings()
    try:
        result = Designer(settings).load_config(
            settings.onec_workspace,
            execute=execute,
            update_db=update_db,
            update_dump_info=True,
        )
        _print_result(result)
        if result.executed and result.returncode != 0:
            raise typer.Exit(result.returncode or 1)
    except DesignerError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


if __name__ == "__main__":
    app()
