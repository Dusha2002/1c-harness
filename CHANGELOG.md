# v0.5 development preview

- Kept the packaged desktop bridge warm between ordinary UI actions, removing repeated PyInstaller startup latency.
- Made workspace status lightweight by counting source files without reading every BSL/XML file.
- Split 1C discovery into explicit user-triggered platform and registered-infobase scans.
- Added IDE-style horizontally resizable and collapsible code/review panel with Monaco auto-layout.

- Replaced the oversized agent prompt with a compact Harness protocol plus lazy skill:// catalog.
- Added built-in onec-engineering and highload-systems skills.
- Added list_skills/load_skill agent tools; full skill bodies enter context only on demand.
- Added per-user Markdown/TXT skill import, deletion and native desktop management UI.
- Skills cannot expand permissions or bypass staging/safety gates.
- Bumped desktop/core version to 0.5.0.

# v0.4 development preview

- Added guided first-run setup with installed-platform and registered-infobase discovery.
- Added automatic staging copy creation for file infobases and native Windows path pickers.
- Added persistent Git-free source baselines after configuration export.

- Replaced demo UI with persistent settings, real tool progress, cancellation and full-file review.
- Added durable failed/interrupted-run recovery, byte-exact reject and external-edit conflict detection.
- Added explicit checked-source deployment with a required .dt backup and separate confirmation.
- Fixed extension patch validation scope, Cyrillic Git filenames and false diff validation after errors.
- Added test/primary identity guards, restricted internal source access, offline Monaco and Windows DPAPI settings.
- Added packaged Python sidecar and Windows installer workflow with packaged-bridge smoke tests.
- Real installed-1C acceptance testing remains required; no 7.7 or active Configurator editor integration.

# Changelog

## 0.3.0

- managed-form semantic builders and form indexing;
- information/accumulation register semantic builders;
- extension-aware Designer, source borrowing and method interceptors;
- independent validation gates for base configuration and extensions;
- stdio MCP server with read-only defaults and explicit source-write opt-in;
- executable Test Client/Test Manager E2E path;
- updated tests and desktop build coverage.
