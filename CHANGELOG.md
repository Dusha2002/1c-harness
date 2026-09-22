# v0.5 development preview

- GigaChat HTTPS now uses the native Windows certificate trust store by default, fixing self-signed-chain failures caused by trusted antivirus/corporate TLS interception and Windows-only root CAs.
- Custom GigaChat PEM/CRT CA bundles still take precedence; TLS verification is never disabled automatically.

- Replaced raw filesystem copies of working 1C infobases with a clean local sandbox created by 1C itself.
- Primary user data remains available to the agent through read-only COM runtime tools; autonomous data writes stay disabled.
- Automatic sandbox setup now exports the primary configuration first, creates an empty file infobase, and loads/updates the configuration there.
- Existing test copies can still be selected explicitly when changed code must be exercised against representative real data.

- First-run setup now automatically scans for installed 1C and registered bases once; failed/interrupted scans can be retried manually.
- Search buttons repaint immediately with independent spinners before the desktop bridge starts scanning.
- Added system/light/dark themes with a quick top-bar toggle, persisted preference, Windows theme following and dark Monaco diff.
- Broadened 1C platform discovery to ProgramW6432, LOCALAPPDATA installs and PATH.

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
