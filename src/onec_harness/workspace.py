from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class WorkspaceError(RuntimeError):
    pass


@dataclass(slots=True)
class SearchMatch:
    path: str
    line: int
    text: str


class Workspace:
    """Filesystem sandbox for exported 1C configuration sources."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser().resolve()

    def ensure_exists(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, relative_path: str | Path) -> Path:
        path = (self.root / relative_path).resolve()
        if path != self.root and self.root not in path.parents:
            raise WorkspaceError(f"Path escapes workspace: {relative_path}")
        return path

    def read_text(self, relative_path: str | Path) -> str:
        return self.resolve(relative_path).read_text(encoding="utf-8-sig")

    def write_text(self, relative_path: str | Path, content: str) -> None:
        path = self.resolve(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def replace_once(self, relative_path: str | Path, old: str, new: str) -> None:
        if not old:
            raise WorkspaceError("old text must not be empty")
        current = self.read_text(relative_path)
        count = current.count(old)
        if count != 1:
            raise WorkspaceError(f"Expected exactly one match, got {count}")
        self.write_text(relative_path, current.replace(old, new, 1))

    def search(self, needle: str, suffixes: tuple[str, ...] = (".bsl", ".xml")) -> list[SearchMatch]:
        if not needle:
            return []
        matches: list[SearchMatch] = []
        lowered = needle.casefold()
        for path in self.root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in suffixes:
                continue
            try:
                lines = path.read_text(encoding="utf-8-sig").splitlines()
            except UnicodeDecodeError:
                continue
            for number, line in enumerate(lines, start=1):
                if lowered in line.casefold():
                    matches.append(
                        SearchMatch(
                            path=str(path.relative_to(self.root)),
                            line=number,
                            text=line.strip(),
                        )
                    )
        return matches

    def git_diff(self) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.root), "diff", "--", "."],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            raise WorkspaceError(result.stderr.strip() or "git diff failed")
        return result.stdout

    def git_restore(self, relative_path: str | Path, *, confirmed: bool = False) -> None:
        if not confirmed:
            raise WorkspaceError("Rollback requires explicit confirmation")
        path = self.resolve(relative_path)
        relative = str(path.relative_to(self.root))
        result = subprocess.run(
            ["git", "-C", str(self.root), "restore", "--", relative],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            raise WorkspaceError(result.stderr.strip() or "git restore failed")
