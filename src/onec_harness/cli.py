from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from onec_harness.onec.designer import CommandResult, Designer, DesignerError
from onec_harness.providers.base import Message, ProviderError
from onec_harness.providers.factory import create_provider
from onec_harness.settings import Settings
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


@app.command()
def doctor() -> None:
    """Show wiring status without exposing secrets."""
    settings = _settings()
    table = Table(title="1C Harness doctor")
    table.add_column("Setting")
    table.add_column("Value")
    table.add_row("LLM provider", settings.llm_provider)
    table.add_row("LLM model", settings.llm_model)
    if settings.provider_name == "gigachat":
        credentials_ok = bool(settings.gigachat_credentials)
    elif settings.provider_name == "anthropic":
        credentials_ok = bool(settings.anthropic_api_key)
    else:
        credentials_ok = bool(settings.llm_api_key)
    table.add_row("LLM credentials", "configured" if credentials_ok else "missing")
    table.add_row("1C executable", str(settings.onec_exe or "not configured"))
    table.add_row("1C connection", "configured" if settings.onec_ib_connection else "missing")
    table.add_row("Workspace", str(settings.onec_workspace.expanduser().resolve()))
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
