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
from onec_harness.onec.designer import CommandResult, Designer, DesignerError
from onec_harness.providers.base import Message, ProviderError
from onec_harness.providers.factory import create_provider
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
        "steps": [
            {"tool": step.tool, "args": step.args, "result": step.result}
            for step in result.steps
        ],
    }


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

    payload = {
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "llm_credentials": credentials_ok,
        "onec_exe": str(settings.onec_exe) if settings.onec_exe else None,
        "onec_connection": bool(settings.onec_ib_connection),
        "workspace": str(settings.onec_workspace.expanduser().resolve()),
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False))
        return

    table = Table(title="1C Harness doctor")
    table.add_column("Setting")
    table.add_column("Value")
    table.add_row("LLM provider", settings.llm_provider)
    table.add_row("LLM model", settings.llm_model)
    table.add_row("LLM credentials", "configured" if credentials_ok else "missing")
    table.add_row("1C executable", str(settings.onec_exe or "not configured"))
    table.add_row("1C connection", "configured" if settings.onec_ib_connection else "missing")
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
                    content=(
                        "Ты инженер по 1С:Предприятие и BSL. Отвечай точно, "
                        "не выдумывай объекты конфигурации и явно отмечай допущения."
                    ),
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
    write: bool = typer.Option(False, "--write", help="Allow the model to stage patches in workspace files"),
    check: bool = typer.Option(False, "--check", help="Allow the agent to run non-destructive 1C validation"),
    max_steps: int = typer.Option(16, "--max-steps", min=1, max=60),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable output for desktop/MCP clients"),
) -> None:
    """Run the selected model through the allowlisted 1C engineering tool loop."""
    settings = _settings()
    workspace = _workspace(settings)

    async def run():
        provider = create_provider(settings)
        designer = Designer(settings) if check else None
        harness = HarnessAgent(
            provider,
            workspace,
            designer=designer,
            allow_writes=write,
            execute_checks=check,
        )
        return await harness.run(task, max_steps=max_steps)

    try:
        result = asyncio.run(run())
    except (ProviderError, WorkspaceError, DesignerError, SnapshotError) as exc:
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
        if len(compact) > 160:
            compact = compact[:157] + "..."
        table.add_row(str(index), step.tool, compact)
    if result.steps:
        console.print(table)
    console.print("[bold]Summary:[/bold]")
    console.print(result.summary)
    if result.snapshots:
        console.print(f"[dim]Snapshots: {', '.join(result.snapshots)}[/dim]")
    if not write:
        console.print("[yellow]Read-only mode: patches were not permitted.[/yellow]")


@app.command("metadata")
def metadata_command(query: str = typer.Argument(""), limit: int = 100) -> None:
    """List indexed 1C metadata objects from the exported configuration."""
    index = ConfigurationIndex.build(_workspace(_settings()).root)
    console.print(index.describe_objects(query=query, limit=limit))


@app.command("symbols")
def symbols_command(query: str, limit: int = 100) -> None:
    """Find BSL procedures/functions by symbol name."""
    index = ConfigurationIndex.build(_workspace(_settings()).root)
    console.print(index.describe_symbols(query=query, limit=limit))


@app.command("read")
def read_file(path: str) -> None:
    """Read a UTF-8 BSL/XML file inside the configured workspace."""
    try:
        console.print(_workspace(_settings()).read_text(path))
    except (WorkspaceError, OSError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@app.command()
def search(query: str, limit: int = 50) -> None:
    """Search BSL/XML sources inside the workspace."""
    matches = _workspace(_settings()).search(query)[:limit]
    for match in matches:
        console.print(f"[cyan]{match.path}:{match.line}[/cyan] {match.text}")
    if not matches:
        console.print("No matches")


@app.command()
def diff() -> None:
    """Show git diff for the workspace."""
    try:
        text = _workspace(_settings()).git_diff()
        console.print(text or "No changes")
    except WorkspaceError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@app.command()
def rollback(path: str, yes: bool = typer.Option(False, "--yes")) -> None:
    """Restore one workspace file from git. Requires --yes."""
    try:
        _workspace(_settings()).git_restore(path, confirmed=yes)
        console.print(f"Restored {path}")
    except WorkspaceError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@app.command("restore-snapshot")
def restore_snapshot(snapshot_id: str, yes: bool = typer.Option(False, "--yes")) -> None:
    """Restore files captured before an agent patch."""
    if not yes:
        console.print("[red]Snapshot restore requires --yes[/red]")
        raise typer.Exit(2)
    try:
        info = SnapshotStore(_workspace(_settings())).restore(snapshot_id)
        console.print(f"Restored {snapshot_id}: {', '.join(info.paths)}")
    except (SnapshotError, WorkspaceError, OSError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@app.command("dump-config")
def dump_config(execute: bool = typer.Option(False, "--execute")) -> None:
    """Dump the 1C configuration to the workspace; dry-run by default."""
    settings = _settings()
    try:
        result = Designer(settings).dump_config(settings.onec_workspace, execute=execute)
        _print_result(result)
        if result.executed and result.returncode != 0:
            raise typer.Exit(result.returncode or 1)
    except DesignerError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@app.command("check-modules")
def check_modules(execute: bool = typer.Option(False, "--execute")) -> None:
    """Run /CheckModules; dry-run by default."""
    try:
        result = Designer(_settings()).check_modules(execute=execute)
        _print_result(result)
        if result.executed and result.returncode != 0:
            raise typer.Exit(result.returncode or 1)
    except DesignerError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@app.command("check-config")
def check_config(execute: bool = typer.Option(False, "--execute")) -> None:
    """Run structural /CheckConfig validation; dry-run by default."""
    try:
        result = Designer(_settings()).check_config(execute=execute)
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
    """Load workspace sources into Designer. DB update additionally requires --yes."""
    if update_db and not yes:
        console.print("[red]--update-db requires explicit --yes[/red]")
        raise typer.Exit(2)
    settings = _settings()
    try:
        result = Designer(settings).load_config(
            Path(settings.onec_workspace),
            execute=execute,
            update_db=update_db,
        )
        _print_result(result)
        if result.executed and result.returncode != 0:
            raise typer.Exit(result.returncode or 1)
    except DesignerError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


if __name__ == "__main__":
    app()
