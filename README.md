# 1C Harness

Model-agnostic AI harness and desktop review app for safe development and automation of **1C:Enterprise**.

The target is a local **“Codex / Claude Code for 1C”**: an agent that understands 1C metadata and BSL, edits configuration sources, validates them in a disposable staging infobase, can inspect runtime data through `V83.COMConnector`, and prepares UI tests for the standard 1C Test Client/Test Manager stack.

## Architecture

```text
GigaChat / OpenAI / DeepSeek / Anthropic / compatible API
                         |
                    HarnessAgent
                         |
      +------------------+--------------------+
      |                  |                    |
 metadata + BSL      Workspace            Runtime 1C
 semantic API         sandbox             adapters
      |                  |                    |
 create objects      snapshot/diff      Designer staging
 add attributes      accept/reject      COMConnector
 create modules                          Test Client/Manager
```

The model receives an allowlisted tool protocol. It never gets arbitrary shell access or direct SQL access to the 1C database.

## What works now — v0.2

### AI / source engineering

- provider abstraction for GigaChat 3 Ultra, OpenAI, DeepSeek, Anthropic and generic OpenAI-compatible APIs;
- safe source workspace with path-escape protection;
- BSL/XML search and reading;
- metadata index for common 1C object kinds;
- BSL procedure/function symbol index;
- exact one-match patches;
- persistent snapshot before every source/metadata mutation;
- git diff including newly-created metadata files;
- explicit snapshot restore and git rollback.

### Semantic metadata API

The agent no longer has to hand-edit raw XML for the common creation path. Current high-level tools include:

```text
create_catalog
create_document_meta
add_attribute
ensure_module
```

`create_catalog` and `create_document_meta` create hierarchical 1C XML objects, generated type UUIDs and the corresponding `Configuration.xml` child entry. `add_attribute` supports basic primitive types plus common 1C reference types. Every operation is snapshotted first.

The generated source is still treated as untrusted until 1C Designer itself loads and validates it.

### Real staging validation

A key safety rule is now enforced: **the autonomous check loop never loads staged sources into the primary infobase**.

Set a disposable copy:

```env
ONEC_STAGING_IB_CONNECTION=/F "C:\1c\harness-staging"
```

Then the agent loop is:

```text
inspect
  -> snapshot
  -> patch / semantic metadata mutation
  -> diff
  -> LoadConfigFromFiles into STAGING (-updateConfigDumpInfo)
  -> CheckModules
  -> CheckConfig
  -> fix and repeat on error
  -> human review
```

With `--check`, the agent cannot finish after a source change until staging load, `CheckModules` and `CheckConfig` have all succeeded.

### COMConnector runtime adapter

On Windows, install the optional runtime dependency:

```powershell
pip install -e ".[windows,dev]"
```

The adapter uses `V83.COMConnector` and exposes read-oriented helpers:

```text
runtime_query
catalog_items
document_items
register_records
```

Queries use the 1C query language through the platform, not SQL against the underlying DBMS.

Runtime mutations exist but are double opt-in:

```env
ONEC_RUNTIME_ALLOW_WRITES=true
```

and the caller must additionally pass `--runtime-write` / `--yes` depending on the command. They are disabled in normal operation.

### Test Client / Test Manager adapter

The harness can build and launch the standard 1C modes:

```powershell
onec-harness test-client
onec-harness test-client --execute
onec-harness test-manager
onec-harness test-manager --execute
```

The Test Client never silently falls back to the primary DB. Configure either:

```env
ONEC_TEST_CLIENT_CONNECTION=/F "C:\1c\ui-test"
```

or let it use `ONEC_STAGING_IB_CONNECTION`.

There is also a small UI-test DSL compiler. A JSON action list such as:

```json
[
  {"action":"execute_command","link":"e1cib/command/Catalog.Оборудование.Create"},
  {"action":"wait_form","title":"Оборудование*"},
  {"action":"set_field","name":"Наименование","value":"Тест"},
  {"action":"click_button","title":"Записать и закрыть"}
]
```

can be compiled to Test Manager BSL:

```powershell
onec-harness compile-test-scenario scenario.json -o generated-test.bsl
```

The generated BSL uses `TestedApplication`, `TestedForm`, `TestedFormField` and `TestedFormButton` rather than screen coordinates.

**Current boundary:** the harness launches Test Client/Test Manager and generates the BSL scenario, but does not yet inject and execute that generated procedure inside an arbitrary Test Manager infobase automatically. That is the next E2E integration step and requires a real Windows + 1C test environment.

## Safety model

- primary-infobase apply is never part of the autonomous validation loop;
- `--check` requires `ONEC_STAGING_IB_CONNECTION`;
- Test Client targets only an explicit test connection or staging;
- the model never receives unrestricted shell access;
- workspace paths cannot escape `ONEC_WORKSPACE`;
- every mutation gets a snapshot before changes are written;
- COM writes are disabled by default and require two independent opt-ins;
- primary configuration load requires explicit `--execute --yes`;
- `.dt` backup is available before an approved primary apply;
- Designer commands have timeouts and captured logs.

## Quick start on Windows

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[windows,dev]"
copy .env.example .env
```

Minimum useful `.env`:

```env
LLM_PROVIDER=gigachat
LLM_MODEL=GigaChat-3-Ultra
GIGACHAT_CREDENTIALS=...

ONEC_EXE=C:\Program Files\1cv8\8.3.xx.xxxx\bin\1cv8.exe
ONEC_IB_CONNECTION=/F "C:\1c\dev"
ONEC_STAGING_IB_CONNECTION=/F "C:\1c\harness-staging"
ONEC_WORKSPACE=C:\projects\my-config-export
```

Useful commands:

```powershell
onec-harness doctor
onec-harness dump-config --execute
onec-harness metadata Заказ
onec-harness symbols Проведение

onec-harness agent "Найди причину ошибки проведения"
onec-harness agent "Исправь проверку остатков" --write --check

onec-harness create-catalog Оборудование --yes
onec-harness add-attribute catalog Оборудование СерийныйНомер --yes
onec-harness stage-config --execute --yes

onec-harness runtime-query "ВЫБРАТЬ ПЕРВЫЕ 10 Код, Наименование ИЗ Справочник.Товары" --fields Код,Наименование
onec-harness backup-infobase C:\backups\before-harness.dt --execute --yes
```

Applying the workspace to the primary infobase remains a separate explicit action:

```powershell
onec-harness load-config --execute --yes
```

Add `--update-db` only when you intentionally want to update the primary database configuration.

## Desktop application

`desktop/` is a Tauri 2 + React/TypeScript application inspired by an IDE/code-agent review workflow.

Current flow:

- left pane: task/chat + visible agent steps;
- right pane: Monaco review of the actual patch fragment or generated workspace diff;
- source mutations run with snapshots;
- when staging is configured, the desktop agent automatically requests the staging validation loop;
- **Принять** leaves the reviewed change in the local workspace;
- **Отклонить** restores every snapshot from the current run in reverse order;
- applying anything to the primary 1C base is still a separate operation.

Run:

```powershell
cd desktop
npm install
npm run tauri dev
```

Set `ONEC_HARNESS_BIN` if `onec-harness` is not available on `PATH`.

## Providers

### GigaChat

Default model: `GigaChat-3-Ultra`.

### OpenAI / DeepSeek / compatible APIs

Use the generic OpenAI-compatible adapter with the desired base URL, API key and model.

### Anthropic

Anthropic uses a dedicated Messages API adapter.

## Roadmap to v1

1. **Test Manager E2E runner** — automatically execute the generated Test Manager scenario and return structured assertions/logs to the agent.
2. **Broader semantic metadata API** — registers, enums, forms, tabular sections, extensions and safe reference/dependency navigation.
3. **Runtime semantics** — richer typed object retrieval and explicit server-function contracts.
4. **MCP server** — expose the same safe tool registry to Codex, Claude, ChatGPT-compatible clients and other agents.
5. **Desktop production flow** — streaming tool events, multiple real diff tabs, staging/apply controls and UI test results.
6. **Windows E2E CI/runner** — automated smoke tests against an installed 1C platform and disposable infobase.

## Status

`v0.2` is an active alpha-oriented prototype. Python and desktop TypeScript/Vite builds are covered by CI. Real COM and Test Client/Test Manager E2E execution must be validated on a Windows host with 1C installed. Use disposable copies for staging/testing and do not point autonomous runtime-write flows at production.
