from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from onec_harness.workspace import Workspace


SNAPSHOT_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class SnapshotError(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class SnapshotInfo:
    snapshot_id: str
    paths: tuple[str, ...]
    created_at: str


class SnapshotStore:
    """Small persistent snapshots for files touched by the agent.

    Snapshots live under the workspace in .onec-harness/snapshots and are intended
    for review/rollback of staged source changes, not as a replacement for Git or
    a full .dt infobase backup.
    """

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        self.root = workspace.root / ".onec-harness" / "snapshots"

    def _path(self, snapshot_id: str) -> Path:
        if not SNAPSHOT_ID_RE.fullmatch(snapshot_id):
            raise SnapshotError("Invalid snapshot id")
        return self.root / f"{snapshot_id}.json"

    def create(self, paths: list[str | Path]) -> SnapshotInfo:
        if not paths:
            raise SnapshotError("Snapshot requires at least one file")
        snapshot_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
        created_at = datetime.now(UTC).isoformat()
        entries: list[dict[str, object]] = []
        normalized_paths: list[str] = []

        for raw_path in paths:
            resolved = self.workspace.resolve(raw_path)
            relative = str(resolved.relative_to(self.workspace.root))
            normalized_paths.append(relative)
            entries.append(
                {
                    "path": relative,
                    "exists": resolved.exists(),
                    "content": resolved.read_text(encoding="utf-8-sig") if resolved.exists() else None,
                }
            )

        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "snapshot_id": snapshot_id,
            "created_at": created_at,
            "entries": entries,
        }
        self._path(snapshot_id).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return SnapshotInfo(snapshot_id=snapshot_id, paths=tuple(normalized_paths), created_at=created_at)

    def restore(self, snapshot_id: str) -> SnapshotInfo:
        snapshot_path = self._path(snapshot_id)
        if not snapshot_path.exists():
            raise SnapshotError(f"Unknown snapshot: {snapshot_id}")
        payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
        entries = payload.get("entries")
        if not isinstance(entries, list):
            raise SnapshotError("Snapshot is malformed")

        restored: list[str] = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise SnapshotError("Snapshot is malformed")
            relative = str(entry.get("path", ""))
            target = self.workspace.resolve(relative)
            exists = bool(entry.get("exists"))
            content = entry.get("content")
            if exists:
                if not isinstance(content, str):
                    raise SnapshotError("Snapshot content is malformed")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            elif target.exists():
                target.unlink()
            restored.append(relative)

        return SnapshotInfo(
            snapshot_id=str(payload.get("snapshot_id", snapshot_id)),
            paths=tuple(restored),
            created_at=str(payload.get("created_at", "")),
        )
