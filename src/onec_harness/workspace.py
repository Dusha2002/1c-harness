from __future__ import annotations

import difflib
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


@dataclass(slots=True, frozen=True)
class FileReview:
    path: str
    original: str
    modified: str


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
            if not path.is_file() or path.suffix.lower() not in suffixes or ".onec-harness" in path.parts:
                continue
            try:
                lines = path.read_text(encoding="utf-8-sig").splitlines()
            except UnicodeDecodeError:
                continue
            for number, line in enumerate(lines, start=1):
                if lowered in line.casefold():
                    matches.append(SearchMatch(str(path.relative_to(self.root)), number, line.strip()))
        return matches

    def _git(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return result

    def changed_paths(self) -> list[str]:
        status = self._git(["status", "--porcelain", "--untracked-files=all"])
        if status.returncode != 0:
            raise WorkspaceError(status.stderr.strip() or "git status failed")
        paths: list[str] = []
        for line in status.stdout.splitlines():
            if len(line) < 4:
                continue
            raw = line[3:].strip().strip('"')
            if " -> " in raw:
                raw = raw.split(" -> ", 1)[1]
            if raw == ".onec-harness" or raw.startswith(".onec-harness/"):
                continue
            if raw not in paths:
                paths.append(raw)
        return paths

    def review_changes(self) -> list[FileReview]:
        reviews: list[FileReview] = []
        for relative in self.changed_paths():
            current_path = self.resolve(relative)
            modified = ""
            if current_path.exists() and current_path.is_file():
                try:
                    modified = current_path.read_text(encoding="utf-8-sig")
                except UnicodeDecodeError:
                    continue
            previous = self._git(["show", f"HEAD:{relative}"])
            original = previous.stdout if previous.returncode == 0 else ""
            reviews.append(FileReview(path=relative, original=original, modified=modified))
        return reviews

    def git_diff(self) -> str:
        result = self._git(["diff", "--", ".", ":(exclude).onec-harness"])
        if result.returncode != 0:
            raise WorkspaceError(result.stderr.strip() or "git diff failed")

        chunks = [result.stdout.rstrip()] if result.stdout.strip() else []
        for review in self.review_changes():
            previous = self._git(["show", f"HEAD:{review.path}"])
            if previous.returncode == 0:
                continue
            unified = difflib.unified_diff(
                [],
                review.modified.splitlines(keepends=True),
                fromfile="/dev/null",
                tofile=f"b/{review.path}",
                lineterm="",
            )
            chunks.append("\n".join(unified))
        return "\n".join(chunk for chunk in chunks if chunk).rstrip() + ("\n" if chunks else "")

    def git_restore(self, relative_path: str | Path, *, confirmed: bool = False) -> None:
        if not confirmed:
            raise WorkspaceError("Rollback requires explicit confirmation")
        path = self.resolve(relative_path)
        relative = str(path.relative_to(self.root))
        result = self._git(["restore", "--", relative])
        if result.returncode != 0:
            raise WorkspaceError(result.stderr.strip() or "git restore failed")
