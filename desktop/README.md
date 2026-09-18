# Desktop development (Windows x64)

End users install the NSIS artifact from the Windows desktop workflow and configure everything in the app.
For local development, from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[windows,dev]" pyinstaller
python scripts/build_sidecar.py
cd desktop
npm ci
npm run tauri dev
```

`npm run tauri build` produces the NSIS installer with a bundled Python sidecar. Rust and Windows C++ build tools
are build-time dependencies only. The development fallback calls `python -m onec_harness.desktop_bridge`;
`ONEC_HARNESS_PYTHON` can select the venv interpreter. Production requires the packaged sidecar.

The browser-only Vite preview cannot call native commands. It displays a connection error rather than fake results.
The bridge uses one JSON request on stdin and NDJSON progress/results on stdout, keeping credentials off process arguments.
Settings and source sessions are per user/project. Secrets are not returned to the webview after saving.
