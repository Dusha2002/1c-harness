from pathlib import Path

from onec_harness.snapshots import SnapshotStore
from onec_harness.workspace import Workspace


def test_snapshot_restores_previous_file_content(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    workspace.write_text("Module.bsl", "before")
    store = SnapshotStore(workspace)

    snapshot = store.create(["Module.bsl"])
    workspace.write_text("Module.bsl", "after")
    restored = store.restore(snapshot.snapshot_id)

    assert restored.paths == ("Module.bsl",)
    assert workspace.read_text("Module.bsl") == "before"


def test_snapshot_removes_file_that_did_not_exist(tmp_path: Path) -> None:
    workspace = Workspace(tmp_path)
    store = SnapshotStore(workspace)

    snapshot = store.create(["Generated.bsl"])
    workspace.write_text("Generated.bsl", "generated")
    store.restore(snapshot.snapshot_id)

    assert not workspace.resolve("Generated.bsl").exists()
