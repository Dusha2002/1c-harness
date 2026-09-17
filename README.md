# 1C Harness

Model-agnostic AI harness and desktop review app for safe development and automation of **1C:Enterprise**.

The target is a local **“Codex / Claude Code for 1C”**: an agent that understands 1C metadata and BSL, edits configuration sources, validates them in a disposable staging infobase, inspects runtime data through `V83.COMConnector`, and can execute UI scenarios through the standard 1C Test Client/Test Manager stack.

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
 objects/attributes  snapshot/diff      Designer staging
 enums/tabulars      accept/reject      COMConnector
                                        Test Client/Manager
```

The model receives an allowlisted tool protocol. It never gets arbitrary shell access or direct SQL access to the 1C database.

## What works now

### AI / source engineering

- GigaChat 3 Ultra, OpenAI, DeepSeek, Anthropic and generic OpenAI-compatible providers;
- safe source workspace with path-escape protection;
- BSL/XML search and metadata/symbol indexing;
- exact one-match patches;
- persistent snapshots before source/metadata mutations;
- git diff and explicit snapshot restore/rollback;
- staging-only autonomous validation with `LoadConfigFromFiles`, `CheckModules` and `CheckConfig`.

### Semantic metadata API

Current high-level mutations include:

```text
create_catalog
create_document_meta
create_enum
add_enum_value
add_attribute
add_tabular_section
ensure_module
```

The common object creation path therefore does not require the model to hand-edit raw XML. Generated metadata is still treated as untrusted until 1C Designer loads and validates it.

### COMConnector runtime

Read-oriented tools use `V83.COMConnector` and the 1C query language rather than direct DBMS SQL:

```text
runtime_query
catalog_items
document_items
register_records
```

Runtime writes are disabled by default and require both `ONEC_RUNTIME_ALLOW_WRITES=true` and an explicit write flag/confirmation.

### Executable Test Manager E2E runner

The harness can compile a JSON UI scenario to BSL and can now also prepare a temporary external data processor that executes the scenario in Test Manager.

The E2E path is:

```text
scenario JSON
   -> ScenarioCompiler (TestedApplication / TestedForm / fields / buttons)
   -> temporary external processor source
   -> Designer /LoadExternalDataProcessorOrReportFromFiles
   -> runner.epf
   -> Test Client /TestClient
   -> wait for test port
   -> Test Manager /TestManager /Execute runner.epf
   -> structured OK / FAILED result
```

Example scenario:

```json
[
  {"action":"execute_command","link":"e1cib/command/Catalog.Оборудование.Create"},
  {"action":"wait_form","title":"Оборудование*"},
  {"action":"set_field","name":"Наименование","value":"Тест"},
  {"action":"click_button","title":"Записать и закрыть"}
]
```

Compile only:

```powershell
onec-harness compile-test-scenario scenario.json -o generated-test.bsl
```

Build/execute the real runner:

```powershell
onec-harness run-test-scenario scenario.json
onec-harness run-test-scenario scenario.json --execute --yes
```

The agent also has the `run_ui_test` tool when started with `--ui-test`.

For changed source/metadata, `run_ui_test` is blocked until `stage_config` has succeeded with `update_db=true`, so the UI test runs against an updated disposable copy rather than stale metadata.

## Safety model

- autonomous source validation never loads into the primary infobase;
- `--check` requires `ONEC_STAGING_IB_CONNECTION`;
- Test Client targets an explicit test connection or staging and never silently falls back to primary;
- generated E2E runner EPFs are compiled through the staging Designer connection;
- the model never receives unrestricted shell access;
- workspace paths cannot escape `ONEC_WORKSPACE`;
- every metadata/source mutation receives a snapshot;
- COM writes require two opt-ins;
- primary configuration load requires explicit `--execute --yes`;
- `.dt` backup is available before an approved primary apply.

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

# For real UI E2E:
ONEC_TEST_MANAGER_CONNECTION=/F "C:\1c\test-manager"
# Optional; otherwise Test Client uses staging:
ONEC_TEST_CLIENT_CONNECTION=/F "C:\1c\ui-test"
```

Useful commands:

```powershell
onec-harness doctor
onec-harness dump-config --execute
onec-harness metadata Заказ
onec-harness symbols Проведение

onec-harness agent "Найди причину ошибки проведения"
onec-harness agent "Исправь проверку остатков" --write --check
onec-harness agent "Исправь форму и проверь её" --write --check --ui-test

onec-harness create-catalog Оборудование --yes
onec-harness create-enum Статусы --values '["Новый","Закрыт"]' --yes
onec-harness add-tabular-section document Заявка Товары --columns '[{"name":"Товар","value_type":"CatalogRef.Товары"}]' --yes
onec-harness stage-config --execute --yes --update-db

onec-harness runtime-query "ВЫБРАТЬ ПЕРВЫЕ 10 Код, Наименование ИЗ Справочник.Товары" --fields Код,Наименование
onec-harness backup-infobase C:\backups\before-harness.dt --execute --yes
```

Applying the workspace to the primary infobase remains a separate explicit action:

```powershell
onec-harness load-config --execute --yes
```

Add `--update-db` only when you intentionally want to update the primary database configuration.

## Desktop application

`desktop/` is a Tauri 2 + React/TypeScript review application:

- left pane: task/chat + visible agent steps;
- right pane: Monaco review of the actual patch fragment or generated workspace diff;
- source mutations run with snapshots;
- staging validation is automatically requested when configured;
- **Принять** leaves the reviewed change in the local workspace;
- **Отклонить** restores snapshots from the run in reverse order;
- applying to the primary 1C base remains separate.

Run:

```powershell
cd desktop
npm install
npm run tauri dev
```

Set `ONEC_HARNESS_BIN` if `onec-harness` is not available on `PATH`.

## Roadmap to v1

1. **Windows E2E validation** — run the new EPF/Test Manager path on a real installed 1C platform and harden startup/cleanup/result collection.
2. **Broader semantic metadata API** — information/accumulation registers, managed forms and configuration extensions.
3. **Richer UI test DSL** — table rows, selections, dialogs, assertions, screenshots/log artifacts and multi-client scenarios.
4. **Runtime semantics** — richer typed retrieval and explicit server-function contracts.
5. **MCP server** — expose the same safe tool registry to Codex, Claude, ChatGPT-compatible clients and other agents.
6. **Desktop production flow** — streaming tool events, multiple diff tabs, staging/apply controls and E2E test result cards.

## Status

The project is an alpha-oriented prototype. Python and desktop TypeScript/Vite builds are covered by CI. Real COM and E2E execution still needs validation on a Windows host with 1C installed. Use disposable copies for staging/testing and do not point autonomous runtime-write flows at production.
