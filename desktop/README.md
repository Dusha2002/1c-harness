# 1C Harness Desktop

Desktop shell for the local 1C Harness. The UI follows a review-first workflow:

1. user describes a task in the left conversation pane;
2. the agent exposes its engineering steps (`metadata`, `search`, `read`, `patch`, checks);
3. staged BSL/XML changes are shown as a diff on the right;
4. the user accepts or rejects the local source change;
5. applying the accepted change to a 1C configuration/test infobase remains a separate explicit action.

## Development

Install the Python harness first from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
```

Then install desktop dependencies:

```powershell
cd desktop
npm install
npm run tauri dev
```

The Tauri bridge launches the `onec-harness` executable without a shell. You can override its location with:

```powershell
$env:ONEC_HARNESS_BIN = "C:\\path\\to\\onec-harness.exe"
```

The current screen contains demo review data so the layout can be developed without a configured 1C installation. The connect button already calls `onec-harness doctor --json`, and chat requests call the read-only agent API. Real staged-patch streaming is the next integration step.
