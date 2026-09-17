from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from onec_harness.onec.designer import Designer
from onec_harness.settings import Settings


app = typer.Typer(no_args_is_help=True, help="1C Harness extension source workflow")
console = Console()


def _settings() -> Settings:
    return Settings()


def _staging(settings: Settings) -> Designer:
    if not settings.onec_staging_ib_connection.strip():
        raise typer.BadParameter("ONEC_STAGING_IB_CONNECTION is required")
    return Designer(settings, connection_override=settings.onec_staging_ib_connection)


def _show(result: object) -> None:
    command = getattr(result, "command", [])
    console.print(" ".join(str(part) for part in command))
    if not getattr(result, "executed", False):
        console.print("[yellow]dry-run[/yellow]")
        return
    ok = getattr(result, "ok", False)
    console.print("[green]OK[/green]" if ok else "[red]FAILED[/red]")
    output = getattr(result, "combined_output")()
    if output:
        console.print(output)
    if not ok:
        raise typer.Exit(1)


@app.command("dump")
def dump_extension(
    name: str,
    target: Path | None = typer.Option(None, "--target"),
    execute: bool = typer.Option(False, "--execute"),
) -> None:
    settings = _settings()
    destination = target or settings.onec_workspace / "Extensions" / name
    _show(Designer(settings).dump_config(destination, execute=execute, extension=name))


@app.command("stage")
def stage_extension(
    name: str,
    source: Path | None = typer.Option(None, "--source"),
    update_db: bool = typer.Option(False, "--update-db"),
    execute: bool = typer.Option(False, "--execute"),
) -> None:
    settings = _settings()
    origin = source or settings.onec_workspace / "Extensions" / name
    _show(
        _staging(settings).load_config(
            origin,
            execute=execute,
            update_db=update_db,
            update_dump_info=True,
            extension=name,
        )
    )


@app.command("check")
def check_extension(
    name: str,
    execute: bool = typer.Option(False, "--execute"),
) -> None:
    designer = _staging(_settings())
    modules = designer.check_modules(execute=execute, extension=name)
    _show(modules)
    if execute and not modules.ok:
        return
    _show(designer.check_config(execute=execute, extension=name))
    _show(designer.check_extension_applicability(name, execute=execute))


@app.command("build")
def build_extension(
    name: str,
    output: Path,
    execute: bool = typer.Option(False, "--execute"),
) -> None:
    _show(Designer(_settings()).dump_cfg(output, execute=execute, extension=name))
