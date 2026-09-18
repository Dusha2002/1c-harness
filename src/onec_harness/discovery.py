"""Windows-first discovery of installed 1C platforms and registered infobases."""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from collections.abc import Iterable

from onec_harness.connections import file_connection_path


def discover_onec_executables() -> list[str]:
    """Return installed 1cv8.exe paths, newest-looking versions first."""
    roots: list[Path] = []
    for variable in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        value = os.environ.get(variable)
        if value:
            roots.append(Path(value) / "1cv8")
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        roots.append(Path(local_appdata) / "Programs" / "1cv8")

    found: list[Path] = []
    on_path = shutil.which("1cv8.exe") or shutil.which("1cv8")
    if on_path:
        found.append(Path(on_path).resolve())
    for root in roots:
        if not root.is_dir():
            continue
        try:
            versions = list(root.iterdir())
        except OSError:
            continue
        for version in versions:
            candidate = version / "bin" / "1cv8.exe"
            if candidate.is_file():
                found.append(candidate.resolve())

    def version_key(path: Path) -> tuple[int, ...]:
        parts = re.findall(r"\d+", path.parent.parent.name)
        return tuple(int(part) for part in parts)

    found.sort(key=version_key, reverse=True)
    result: list[str] = []
    for path in found:
        value = str(path)
        if value not in result:
            result.append(value)
    return result


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1].replace('""', '"')
    return value


def _parts(connect: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for match in re.finditer(r'(?i)([A-Za-z]+)\s*=\s*("(?:[^"]|"")*"|[^;]*)(?:;|$)', connect):
        result[match.group(1).casefold()] = _unquote(match.group(2))
    return result


def connection_from_registration(connect: str) -> str | None:
    values = _parts(connect)
    file_path = values.get("file")
    if file_path:
        return f'/F "{file_path}"'
    server = values.get("srvr")
    reference = values.get("ref")
    if server and reference:
        return f'/S "{server}\\{reference}"'
    return None


def parse_ibases(text: str) -> list[dict[str, str | None]]:
    """Parse 1CEStart ibases.v8i without executing any 1C code."""
    current_name = ""
    result: list[dict[str, str]] = []
    for raw in text.lstrip("\ufeff").splitlines():
        line = raw.strip()
        if not line or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current_name = line[1:-1].strip()
            continue
        key, separator, value = line.partition("=")
        if not separator or key.strip().casefold() != "connect":
            continue
        connection = connection_from_registration(value.strip())
        if connection:
            result.append({
                "name": current_name or connection,
                "connection": connection,
                "file_path": file_connection_path(connection),
            })
    return result


def _registration_files() -> Iterable[Path]:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return ()
    root = Path(appdata) / "1C" / "1CEStart"
    return (
        root / "ibases.v8i",
        root / "common" / "ibases.v8i",
    )


def discover_infobases() -> list[dict[str, str | None]]:
    found: list[dict[str, str | None]] = []
    seen: set[str] = set()
    for path in _registration_files():
        if not path.is_file():
            continue
        try:
            entries = parse_ibases(path.read_text(encoding="utf-8-sig", errors="replace"))
        except OSError:
            continue
        for entry in entries:
            identity = entry["connection"].casefold()
            if identity not in seen:
                seen.add(identity)
                found.append(entry)
    found.sort(key=lambda item: item["name"].casefold())
    return found
